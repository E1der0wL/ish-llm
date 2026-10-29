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
from hub.backend.tools.validation import ToolInputRegistry, ReadPageSize
from hub.config.defaults import HubDefaults
from llm.components.tools import Tool, ToolExposure, ToolRegistry
from llm.core.model_protocol import ModelMessage, ModelRole, ToolCall, ToolResult
from llm.core.models import Project, Run
from llm.core.tool_contracts import ToolEffect
from dataclasses import replace
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.support.runtime_tools import TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class ConstraintTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_sizes_preserve_all_matches_and_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            # Matching must inspect the full line even when the returned snippet
            # ends before the query. Each page must resume at the next match.
            content = ''.join(f'{index:02d} ' + 'x' * 500 + ' needle\n' for index in range(47))
            source = Path(directory) / 'matches.txt'
            source.write_text(content)
            builtins = HubBuiltinTools(directory)
            self.addAsyncCleanup(builtins.close)
            registry = ToolInputRegistry.observe(builtins.toolkit.registry)
            constraints = deepcopy(HubDefaults.TOOL_PAGE_CONSTRAINTS)
            offset, matches = 0, []
            while True:
                supplied = dict(path='.', query='needle', recursive=False, case_sensitive=True,
                    limit=1000000, max_chars=1000000, offset=offset, context_lines=1)
                tool, actual = registry.prepare('file_search', supplied, constraints=constraints)
                self.assertEqual((actual['limit'], actual['max_chars']), (20, 400))
                self.assertEqual((supplied['limit'], supplied['max_chars']), (1000000, 1000000))
                self.assertEqual(actual['offset'], offset)
                result = await tool.handler(actual)
                matches.extend(result['matches'])
                for match in result['matches']:
                    self.assertNotIn('needle', match['text'])
                    self.assertLessEqual(len(match['text']), 400)
                    self.assertTrue(all(len(line) <= 400 for line in match['context']))
                if not result['truncated']:
                    self.assertIsNone(result['next_offset'])
                    break
                self.assertEqual(result['next_offset'], offset + len(result['matches']))
                offset = result['next_offset']
            self.assertEqual([m['line'] for m in matches], list(range(1, 48)))
            self.assertEqual(len({m['sha256'] for m in matches}), 1)
            self.assertEqual(source.read_text(), content)

    async def test_listing_overflow_preserves_truncation_and_filtered_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(205):
                (root / f'{index:03d}.txt').write_text(str(index))
            builtins = HubBuiltinTools(root)
            self.addAsyncCleanup(builtins.close)
            registry = ToolInputRegistry.observe(builtins.toolkit.registry)
            constraints = deepcopy(HubDefaults.TOOL_PAGE_CONSTRAINTS)
            tool, actual = registry.prepare('file_list',
                {'recursive': False, 'limit': 1000000}, constraints=constraints)
            result = await tool.handler(actual)
            self.assertEqual(actual['limit'], 200)
            self.assertEqual(len(result['entries']), 200)
            self.assertTrue(result['truncated'])
            # Native file_list has no offset: narrow the selection, never invent
            # a page cursor or misrepresent this bounded listing as complete.
            self.assertNotIn('next_offset', result)
            tool, actual = registry.prepare('file_list',
                {'recursive': False, 'limit': 1000000, 'include': ['20*.txt']}, constraints=constraints)
            rest = await tool.handler(actual)
            self.assertFalse(rest['truncated'])
            self.assertEqual({r['path'] for r in rest['entries']}, {f'{i:03d}.txt' for i in range(200, 205)})
            self.assertEqual(len(list(root.glob('*.txt'))), 205)

    async def test_read_size_normalization_cannot_repair_other_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            builtins = HubBuiltinTools(directory)
            self.addAsyncCleanup(builtins.close)
            original = builtins.toolkit.registry.get('file_list')
            handler = AsyncMock()
            tool = replace(original, handler=handler)
            registry = ToolInputRegistry((tool,))
            args = {'recursive': False, 'limit': 1000000}
            # Explicit fixed/selectable choices, non-page limits, and native
            # schema bounds remain authoritative after page normalization.
            for constraints in (
                {'file_list': {'limit': {'mode': 'fixed', 'value': 20}}},
                {'file_list': {'limit': {'mode': 'selectable', 'values': [10, 20]}}},
                {'file_list': {'limit': {'mode': 'bounded', 'minimum': 1, 'maximum': 20},
                    'max_file_bytes': {'mode': 'bounded', 'maximum': 10}}},
            ):
                supplied = {**args, 'max_file_bytes': 100}
                with self.subTest(constraints=constraints), self.assertRaises(ValueError):
                    registry.prepare('file_list', supplied, constraints=constraints)
            policy = {'file_list': {'limit': {'mode': 'bounded', 'maximum': 20}}}
            schema = deepcopy(tool.parameters)
            schema['properties']['limit']['maximum'] = 5
            narrower = ToolInputRegistry((replace(tool, parameters=schema),))
            with self.assertRaises(ValueError):
                narrower.prepare('file_list', args, constraints=policy)
            mutating = ToolInputRegistry((replace(tool,
                contract=replace(tool.contract, effect=ToolEffect.EXTERNAL)),))
            with self.assertRaises(ValueError):
                mutating.prepare('file_list', args, constraints=policy)
            self.assertEqual(registry.prepare('file_list', args)[1], args)
            handler.assert_not_called()

    async def test_oversized_file_pages_keep_source_and_exact_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            content = '가나다\n' * 6500
            source = Path(directory) / 'long.txt'
            source.write_text(content)
            builtins = HubBuiltinTools(directory)
            self.addAsyncCleanup(builtins.close)
            registry = ToolInputRegistry.observe(builtins.toolkit.registry)
            constraints = {'file_read': {'max_chars': {
                'mode': 'bounded', 'minimum': 1, 'maximum': 3000}}}
            parts, offset = [], 0
            while offset < len(content):
                arguments = {'path': 'long.txt', 'offset': offset, 'max_chars': 1000000}
                tool, actual = registry.prepare('file_read', arguments, constraints=constraints)
                self.assertEqual(arguments['max_chars'], 1000000)
                self.assertEqual(actual['max_chars'], 3000)
                page = await tool.handler(actual)
                parts.append(page['text'])
                self.assertLessEqual(len(page['text']), 3000)
                offset += len(page['text'])
                self.assertEqual(page['next_offset'], offset if offset < len(content) else None)
            self.assertEqual(''.join(parts), content)
            self.assertEqual(source.read_text(), content)
            decimal_bound = {'file_read': {'max_chars': {
                'mode': 'bounded', 'minimum': 1, 'maximum': 3000.75}}}
            _, actual = registry.prepare('file_read', arguments, constraints=decimal_bound)
            self.assertEqual(actual['max_chars'], 3000)
            self.assertIs(type(actual['max_chars']), int)
            for changes in ({'max_chars': '1000000'}, {'max_chars': True},
                            {'max_chars': 0}, {'start_line': 2}, {'unknown': 1}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    registry.prepare('file_read', {**arguments, **changes}, constraints=constraints)
            fixed = {'file_read': {'max_chars': {'mode': 'fixed', 'value': 3000}}}
            with self.assertRaises(ValueError):
                registry.prepare('file_read', arguments, constraints=fixed)

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
                            if key in ReadPageSize.FIELDS.get(name, ()) and invalid == value + 1:
                                _, actual = registry.prepare(name, {**prepared, key: invalid}, constraints=constraints)
                                self.assertEqual(actual[key], value)
                                Draft202012Validator(definition.parameters).validate(actual)
                                continue
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
            [chunk(calls=[call('{"path":"sample.txt","max_chars":100000}', name='file_read', call_id='read')], finish='tool_calls')],
            [chunk(calls=[call('{"tool_query":"file_list"}', name='hub_help', call_id='find_list')], finish='tool_calls')],
            [chunk(calls=[call('{"recursive":false,"limit":1000000}', name='file_list', call_id='list'),
                call('{"query":"sample","case_sensitive":false,"recursive":false,"limit":1000000,"max_chars":1000000}',
                    name='file_search', call_id='search', index=1)], finish='tool_calls')],
            [chunk('The file contains sample data.', finish='stop')])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)/'work'
            work.mkdir()
            (work/'sample.txt').write_text('sample data')
            runtime = HubRuntime(HubConfig(Path(directory)/'storage', file_root=work, auto_title=False,
                model='test/constraints', agent_definition={'engine': 'loop',
                    'tools': ['hub_help', 'file_read', 'file_list', 'file_search']},
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
            self.assertEqual(sum(step.kind == 'tool' and str(step.status) == 'completed' for step in steps), 5)
