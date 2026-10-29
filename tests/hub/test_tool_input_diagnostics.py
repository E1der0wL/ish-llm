"""Late, uncommon Tool inputs retain schema, authorization and effect boundaries."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from jsonschema import Draft202012Validator, ValidationError

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.tools.builtin import HubBuiltinTools
from hub.backend.tools.resources import HubRAGComponent
from hub.backend.tools.validation import ToolInputRegistry
from hub.config.defaults import HubDefaults
from llm.components.tools import Tool, ToolRegistry
from llm.core.configuration_views import ConfigurationResolution
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.support.runtime_tools import TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class ToolInputTests(unittest.IsolatedAsyncioTestCase):
    def test_observation_copies_validated_state_without_registering_tools_again(self):
        class Handler:
            async def __call__(self, arguments):
                return arguments

        handler = Handler()
        tool = Tool('query', 'Query', {'type': 'object', 'properties': {
            'count': {'type': 'integer'}}, 'required': ['count']}, handler, contract=TEST_CONTRACT)
        source = ToolRegistry((tool,))
        with patch.object(ToolRegistry, 'register', side_effect=AssertionError('registered twice')):
            observed = ToolInputRegistry.observe(source)
        self.assertIsInstance(observed, ToolInputRegistry)
        self.assertIsNot(observed, source)
        self.assertIs(observed.get('query').handler, handler)
        self.assertEqual(observed.execution_binding({}), source.execution_binding({}))
        self.assertIs(ToolInputRegistry.observe(observed), observed)
        # Native registry consumers retain their normal subtype contract.
        merged = ToolRegistry()
        merged.extend(observed)
        self.assertEqual(merged.contracts(), source.contracts())
        source.register(Tool('other', '', {'type': 'object'}, handler, contract=TEST_CONTRACT))
        self.assertEqual(observed.names(), ('query',))
        detached = observed.get('query')
        detached.parameters['properties']['count']['type'] = 'string'
        self.assertEqual(observed.get('query').parameters, source.get('query').parameters)
        with self.assertRaises(ValueError):
            observed.register(tool)
        with self.assertRaisesRegex(ValueError, 'count: expected type integer'):
            observed.prepare('query', {'count': 'bad'})

    def test_selection_validates_once_and_remains_detached(self):
        handler = AsyncMock()
        source = ToolRegistry((Tool('query', '', {'type': 'object'}, handler, contract=TEST_CONTRACT),))
        observed = ToolInputRegistry.observe(source)
        original = ToolRegistry.register
        with patch.object(ToolRegistry, 'register', autospec=True, side_effect=original) as register:
            selected = observed.select(('query',))
        self.assertEqual(register.call_count, 1)
        self.assertIsInstance(selected, ToolInputRegistry)
        self.assertIs(selected.get('query').handler, handler)
        selected.register(Tool('extra', '', {'type': 'object'}, handler, contract=TEST_CONTRACT))
        self.assertEqual(observed.names(), ('query',))
        for names in (('missing',), ('query', 'query')):
            with self.subTest(names=names), self.assertRaises(ValueError):
                observed.select(names)

    def test_custom_registry_validation_is_not_flattened(self):
        class CustomRegistry(ToolRegistry):
            def prepare(self, name, arguments, *, constraints=None):
                raise ValueError('Application admission denied')

        custom = CustomRegistry()
        self.assertIs(ToolInputRegistry.observe(custom), custom)
        with self.assertRaisesRegex(ValueError, 'Application admission denied'):
            ToolInputRegistry.observe(custom).prepare('query', {})

    async def test_character_and_line_pages_preserve_real_reads_and_constraints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.txt'
            content = 'first\nsecond\n' + 'last\n' * 2000
            path.write_text(content)
            builtins = HubBuiltinTools(directory)
            self.addAsyncCleanup(builtins.close)
            toolkit = builtins.toolkit
            registry = ToolInputRegistry.observe(toolkit.registry).select(('file_read',))
            original = deepcopy(registry.get('file_read').parameters)
            limits = {'file_read': HubDefaults.TOOL_PAGE_CONSTRAINTS['file_read']}
            tool, args = registry.prepare('file_read', {'path': 'sample.txt'}, constraints=limits)
            page = await tool.handler(args)
            self.assertEqual(page['text'], content[:8000])
            self.assertEqual(page['next_offset'], 8000)
            _, next_args = registry.prepare('file_read', {'path': 'sample.txt', 'offset': 8000}, constraints=limits)
            self.assertEqual((await tool.handler(next_args))['text'], content[8000:])
            _, line_args = registry.prepare('file_read', {'path': 'sample.txt', 'start_line': 2, 'max_lines': 1})
            self.assertIn('second', (await tool.handler(line_args))['text'])
            for extra in ({'max_lines': 1}, {'tail_lines': 1}, {'start_line': 2}, {'max_chars': 2}):
                with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'file_read'):
                    registry.prepare('file_read', {'path': 'sample.txt', **extra}, constraints=limits)
            with self.assertRaisesRegex(ValueError, 'tail_lines'):
                registry.prepare('file_read', {'path': 'sample.txt', 'tail_lines': 1, 'max_lines': 1})
            definition, = registry.definitions(constraints=limits)
            validator = Draft202012Validator(definition.parameters)
            validator.validate({'path': 'sample.txt', 'max_chars': 8000})
            with self.assertRaises(ValidationError):
                validator.validate({'path': 'sample.txt', 'max_chars': 8000, 'max_lines': 1})
            self.assertEqual(registry.get('file_read').parameters, original)

    async def test_diagnostics_keep_handler_contract_and_do_not_execute_invalid_inputs(self):
        effects = []
        async def handler(arguments):
            effects.append(arguments)
            return {'ok': True}
        tool = Tool('query', 'Query', {'type': 'object', 'properties': {
            'count': {'type': 'integer', 'minimum': 1}}, 'required': ['count'],
            'additionalProperties': False}, handler, contract=TEST_CONTRACT)
        source = ToolRegistry((tool,))
        registry = ToolInputRegistry.observe(source).select(('query',))
        self.assertEqual(registry.contracts(), source.contracts())
        self.assertEqual(registry.definitions(), source.definitions())
        self.assertIs(registry.get('query').handler, handler)
        for args, message in (({}, 'missing required fields: count'),
                              ({'count': 'secret payload'}, 'count: expected type integer'),
                              ({'count': 1, 'unused': 'secret payload'}, 'unknown fields: unused'),
                              ({'count': -1}, 'count: violates minimum')):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, message) as error:
                registry.prepare('query', args)
            self.assertIn('query', str(error.exception))
            self.assertNotIn('secret payload', str(error.exception))
        with self.assertRaisesRegex(ValueError, 'missing_tool.*not available'):
            registry.prepare('missing_tool', {})
        self.assertEqual(effects, [])
        selected, args = registry.prepare('query', {'count': 3})
        self.assertEqual(await selected.handler(args), {'ok': True})
        self.assertEqual(effects, [{'count': 3}])

    async def test_rag_reranker_enabled_is_unchanged_and_unavailable_choice_is_explicit(self):
        # Consume the backend's capability schema, not an inferred model name.
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                configured = {'method': 'hybrid', 'limit': 5, 'candidate_count': 20, 'rerank': True,
                              'expand': 'chunk', 'max_hops': 1, 'relation_limit': 30}
                data = NS(name='rag', resolve_config=lambda: ConfigurationResolution(
                    values={'config': {'search': configured}}).to_dict(),
                    has_reranker=lambda: enabled, asearch=AsyncMock(return_value={'documents': []}))
                component = HubRAGComponent()
                registry = ToolInputRegistry.observe(component.resolve_runtime(
                    NS(), 'tools', data_factory=lambda _: data))
                if enabled:
                    tool, args = registry.prepare('rag_search', {'query': 'successful retrieval'})
                else:
                    with self.assertRaisesRegex(ValueError, 'rag_search.*rerank'):
                        registry.prepare('rag_search', {'query': 'successful retrieval'})
                    tool, args = registry.prepare('rag_search', {'query': 'successful retrieval', 'rerank': False})
                await tool.handler(args)
                data.asearch.assert_awaited_once_with(**args)
                self.assertTrue(configured['rerank'])

    async def test_late_invalid_tool_names_the_field_without_replaying_completed_calls(self):
        # Several successful model/tool cycles precede the uncommon bad call.
        responses = [[chunk(calls=[call('{"path":"sample.txt"}', name='file_read', call_id=f'read_{index}')], finish='tool_calls')]
                     for index in range(3)]
        responses.append([chunk(calls=[call('{"path":"sample.txt","offset":"wrong"}', name='file_read', call_id='bad_read')], finish='tool_calls')])
        provider = ScriptedCompletion(*responses)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / 'work'
            work.mkdir()
            (work / 'sample.txt').write_text('Read successfully.')
            runtime = HubRuntime(HubConfig(Path(directory) / 'storage', file_root=work,
                model='test/model', auto_title=False, agent_definition={'engine': 'loop', 'tools': ['file_read']},
                engine_factories={'loop': lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            session = await runtime.project.sessions.acreate(agent_ids=['assistant'])
            run, = await (await session.run.submit('Inspect the requested file', recipients=['assistant'])).wait(timeout=45)
            self.assertEqual(str(run.data.status), 'failed')
            self.assertEqual(len(provider.requests), 4, run.data.error)
            steps = await run.steps.alist()
            completed = [step for step in steps if step.kind == 'tool' and str(step.status) == 'completed']
            self.assertEqual(len(completed), 3)
            evidence = json.dumps([step.diagnostic.to_dict() for step in steps if step.diagnostic])
            self.assertIn('file_read', evidence)
            self.assertIn('offset: expected type integer', evidence)
