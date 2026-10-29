"""Model schemas and discovery observe the same admitted Tool constraints."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS

from jsonschema import Draft202012Validator

from hub.backend.execution.binding_cache import BindingCache
from hub.backend.execution.request_profile import RequestMode
from hub.backend.execution.tool_exposure import HubToolSelector
from hub.backend.execution.tool_lookup import ToolLookup
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.tools.builtin import HubBuiltinTools
from hub.backend.tools.validation import ToolInputRegistry
from hub.config.defaults import HubDefaults
from llm.components.tools import Tool, ToolExposure, ToolRegistry
from llm.core.model_protocol import ModelMessage, ModelRole, ToolCall, ToolResult
from llm.core.models import Project, Run
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.support.runtime_tools import TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class ConstraintTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_default_page_constraints_are_explicit_and_enforced(self):
        arguments = {
            'file_list': {'recursive': False},
            'file_read': {'path': 'sample.txt'},
            'file_search': {'query': 'sample', 'case_sensitive': False, 'recursive': False},
            'process_output': {'process_id': 'example'},
        }
        with tempfile.TemporaryDirectory() as directory:
            builtins = HubBuiltinTools(directory)
            self.addAsyncCleanup(builtins.close)
            registry = ToolInputRegistry.observe(builtins.toolkit.registry)
            constraints = deepcopy(HubDefaults.TOOL_PAGE_CONSTRAINTS)
            self.assertEqual(set(arguments), set(constraints))
            originals = {name: registry.get(name).parameters for name in arguments}
            with patch.object(ToolRegistry, 'register', side_effect=AssertionError('Re-registration')):
                definitions = registry.definitions(names=tuple(arguments), constraints=constraints)
                lookup = ToolLookup(registry, constraints=constraints)
            for definition in definitions:
                name = definition.name
                self.assertEqual(lookup.search(name)['tools'][0]['parameters'], definition.parameters)
                supplied = {**arguments[name], **{key: rule['maximum']
                    for key, rule in constraints[name].items()}}
                _, prepared = registry.prepare(name, supplied, constraints=constraints)
                Draft202012Validator(definition.parameters).validate(prepared)
                for key, rule in constraints[name].items():
                    with self.subTest(tool=name, field=key):
                        field = definition.parameters['properties'][key]
                        value = rule['maximum']
                        self.assertEqual(field['minimum'], 1)
                        self.assertEqual(field['maximum'], value)
                        self.assertNotIn('const', field)
                        self.assertIn('allOf', field)
                        self.assertEqual(prepared[key], value)
                        for smaller in (1, value - 1, value):
                            _, actual = registry.prepare(name, {**prepared, key: smaller}, constraints=constraints)
                            self.assertEqual(actual[key], smaller)
                            Draft202012Validator(definition.parameters).validate(actual)
                        for invalid in (0, -1, value + 1, 1.5, None):
                            with self.assertRaisesRegex(ValueError, name):
                                registry.prepare(name, {**prepared, key: invalid}, constraints=constraints)
                        with self.assertRaisesRegex(ValueError, 'missing required fields'):
                            registry.prepare(name, {k: v for k, v in prepared.items() if k != key}, constraints=constraints)
                self.assertEqual(registry.get(name).parameters, originals[name])
            self.assertEqual(constraints, HubDefaults.TOOL_PAGE_CONSTRAINTS)

    def test_general_policies_preserve_native_validation_and_do_not_leak_values(self):
        handler = AsyncMock(spec=lambda arguments: None)
        registry = ToolInputRegistry((Tool('read', 'Read', {'type': 'object', 'properties': {
            'limit': {'type': 'integer', 'minimum': 1}}, 'required': ['limit'],
            'additionalProperties': False}, handler, contract=TEST_CONTRACT),))
        for rule in ({'mode': 'fixed', 'value': 5}, {'mode': 'selectable', 'values': [3, 5]},
                     {'mode': 'bounded', 'minimum': 2, 'maximum': 5}):
            constraints = {'read': {'limit': rule}}
            definition, = registry.definitions(constraints=constraints)
            validator = Draft202012Validator(definition.parameters)
            for value in (-1, 0, 1, 2, 3, 4, 5, 6, 2.5, 'private value', None):
                arguments = {'limit': value}
                accepted = validator.is_valid(arguments)
                try:
                    registry.prepare('read', arguments, constraints=constraints)
                except ValueError as error:
                    self.assertFalse(accepted, (rule, value))
                    self.assertNotIn('private value', str(error))
                else:
                    self.assertTrue(accepted, (rule, value))
        handler.assert_not_called()

    async def test_bound_discovery_and_selector_share_constraints_without_per_call_derivation(self):
        handler = AsyncMock(spec=lambda arguments: None)
        source = ToolInputRegistry(tuple(Tool(name, 'Query', {'type': 'object', 'properties': {
            'limit': {'type': 'integer'}}}, handler, contract=TEST_CONTRACT)
            for name in ('hub_help', 'company_query')))
        constraints = {'company_query': {'limit': {'mode': 'fixed', 'value': 7}},
                       'not_allowed': {'limit': {'mode': 'fixed', 'value': 200}}}
        bound = ToolInputRegistry.observe(ToolLookup.bind(source, constraints=constraints))
        selector = HubToolSelector(bound, RequestMode.LOOKUP, discovery=True, constraints=constraints)
        before = selector.revision
        constraints['company_query']['limit']['value'] = 8
        with patch.object(ToolRegistry, 'definitions', side_effect=AssertionError('Per-call schema derivation')):
            arguments = {'tool_query': 'company_query'}
            result = await bound.get('hub_help').handler(arguments)
            messages = (ModelMessage(ModelRole.USER, 'Find a company query tool'),
                ModelMessage(ModelRole.ASSISTANT, calls=(ToolCall('find', 'hub_help', arguments),)),
                ModelMessage(ModelRole.TOOL, result=ToolResult('find', json.dumps(result), 'hub_help')))
            self.assertIn('company_query', await selector.select_tools(ToolExposure(1, messages, bound.names(), False)))
        schema = result['tools'][0]['parameters']
        self.assertEqual(schema['properties']['limit']['const'], 7)
        self.assertEqual(selector.revision, before)
        self.assertNotEqual(before, HubToolSelector(source, RequestMode.LOOKUP,
            discovery=True, constraints=constraints).revision)
        self.assertNotEqual(bound.execution_binding({}), ToolLookup.bind(source,
            constraints=constraints).execution_binding({}))
        handler.assert_not_called()

    def test_binding_cache_observes_scope_constraints_separately_from_project(self):
        registry = ToolRegistry((Tool('read', '', {'type': 'object'},
            AsyncMock(spec=lambda arguments: None), contract=TEST_CONTRACT),))
        context = NS(project=Project('p', 'P', None), session=NS(id='s'),
                     run=Run('r', 's', 'u', 'a', 'loop', None, agent_id='assistant'), tools=registry)
        cache, build = BindingCache(), Mock(return_value=registry)
        constraints = {'read': {'limit': {'mode': 'fixed', 'value': 20}}}
        cache.bind(context, None, build, constraints=constraints)
        cache.bind(context, None, build, constraints=deepcopy(constraints))
        self.assertEqual(build.call_count, 1)
        constraints['read']['limit']['value'] = 10
        cache.bind(context, None, build, constraints=constraints)
        self.assertEqual(build.call_count, 2)

    async def test_real_loop_help_and_provider_agree_then_execute_file_tools(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"tool_query":"file_read"}', name='hub_help', call_id='find_read')], finish='tool_calls')],
            [chunk(calls=[call('{"path":"sample.txt","max_chars":100}', name='file_read', call_id='read')], finish='tool_calls')],
            [chunk(calls=[call('{"tool_query":"file_list"}', name='hub_help', call_id='find_list')], finish='tool_calls')],
            [chunk(calls=[call('{"recursive":false,"limit":10}', name='file_list', call_id='list')], finish='tool_calls')],
            [chunk('The file contains sample data.', finish='stop')])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)/'work'
            work.mkdir()
            (work/'sample.txt').write_text('sample data')
            runtime = HubRuntime(HubConfig(Path(directory)/'storage', file_root=work, auto_title=False,
                model='test/constraints', agent_definition={'engine': 'loop',
                    'tools': ['hub_help', 'file_read', 'file_list']},
                engine_factories={'loop': lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            sid = await runtime.new_session()
            await runtime.submit(sid, 'Read sample.txt and list the directory')
            await runtime.sessions[sid].run.wait_idle()
            run, = await runtime.sessions[sid].run.alist()
            record = await run.aget_data()
            self.assertEqual(str(record.status), 'completed', record.error)
            self.assertEqual(len(provider.requests), 5)
            for index, name in ((1, 'file_read'), (3, 'file_list')):
                request = provider.requests[index]
                schema = next(item['function']['parameters'] for item in request['tools']
                              if item['function']['name'] == name)
                lookup = next(json.loads(message['content']) for message in reversed(request['messages'])
                              if message['role'] == 'tool')
                self.assertEqual(lookup['tools'][0]['parameters'], schema)
            steps = await run.steps.alist()
            self.assertEqual(sum(step.kind == 'tool' and str(step.status) == 'completed' for step in steps), 4)
