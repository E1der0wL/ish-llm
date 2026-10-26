"""Agent model values and Engine execution policy have separate owners."""
import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, ValidationError

from llm.core.models import ProjectConfig, RunStatus
from llm.engines.loop import LoopEngine
from llm.engines.registry import EngineRegistry
from llm.llm import LargeLanguageModel
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.services.command_line import CommandLine
from tests.llm.test_loop import ScriptedCompletion, chunk


class AgentModelContractTests(unittest.TestCase):
    def test_cli_separates_model_options_from_execution_policy(self):
        parser = CommandLine.parser('test', engine='loop', workspace='unused')
        required = ['--engine', 'loop', '--model', 'test/model', '--prompt', 'request']
        omitted = parser.parse_args(required)
        self.assertEqual(CommandLine.agent_definition(omitted)['completion'], {'model': 'test/model'})
        explicit = parser.parse_args(required + ['--temperature', '0', '--api-base', 'http://test.invalid',
                                                '--timeout', '30', '--max-iterations', '4'])
        self.assertEqual(CommandLine.agent_definition(explicit)['completion'],
                         {'model': 'test/model', 'temperature': 0, 'api_base': 'http://test.invalid'})
        self.assertEqual(CommandLine.parameters(explicit).to_dict(),
                         {'policy': {'max_iterations': 4, 'request_timeout': 30}})

    def test_engine_schema_has_execution_policy_but_no_model_settings(self):
        engine = LoopEngine()
        schema = engine.describe_config()
        self.assertNotIn('completion', schema['properties']['config']['properties'])
        self.assertNotIn('system_prompt', schema['properties']['config']['properties'])
        self.assertIn('max_iterations', schema['properties']['policy']['properties'])
        # Input budgeting remains distinct from SDK max_tokens.
        self.assertIn('completion', schema['properties']['policy']['properties'])
        self.assertEqual(engine.resolve_config({}, 'loop')['values'], {})
        self.assertFalse(hasattr(engine, 'completion_kwargs'))
        self.assertFalse(hasattr(engine, 'system_prompt'))

    def test_prompt_is_agent_input_and_removed_engine_paths_fail_fast(self):
        registry = EngineRegistry()
        engine = LoopEngine()
        registry.register('writer', engine)
        for prompt in (None, '', 'instructions'):
            with self.subTest(prompt=prompt):
                options = {'config': {'system_prompt': prompt}}
                with self.assertRaises(ValueError):
                    registry.validate_config(ProjectConfig(parameters={'engines': {'writer': options}}))
                with self.assertRaises(ValueError):
                    registry.validate_config(ProjectConfig(), session_config={'parameters': {'engines': {'writer': options}}})
                with self.assertRaises(ValidationError):
                    engine.for_agent({'engine': 'writer', 'system_prompt': 'valid agent prompt', 'engine_options': options})
                selected = engine.for_agent({'engine': 'writer', 'system_prompt': prompt})
                self.assertEqual(selected.resolve_config({}, 'writer')['values'], {})
                self.assertEqual(selected.resolve_config({}, 'writer')['sources'], {})
                self.assertEqual(selected.resolve_config({}, 'writer')['editable'], {})
        self.assertIn('system_prompt', engine.describe_agent()['properties'])

    def test_resource_instructions_compose_without_engine_configuration_lookup(self):
        from unittest.mock import patch
        from llm.engines.loop.configuration import LoopAgentOptions
        engine = LoopEngine().for_agent({'engine': 'loop', 'system_prompt': 'inline'})
        with patch.object(engine, 'resolve_config', side_effect=AssertionError('instructions are not Engine config')):
            composed = engine.with_instructions(None, before=('prompt resource',), after=('skill resource',))
        self.assertEqual(LoopAgentOptions(composed._agent_options).prompt,
                         'prompt resource\n\ninline\n\nskill resource')
        self.assertEqual(LoopAgentOptions(engine._agent_options).prompt, 'inline')

    def test_model_values_rejected_in_project_session_and_agent_engine_options(self):
        engine = LoopEngine()
        registry = EngineRegistry()
        registry.register('writer', engine)
        for options in ({'completion': {'model': 'test/model'}}, {'completion': {}},
                        {'model': 'test/model'}, {'api_key': 'test-only'},
                        {'api_base': 'http://test.invalid'}, {'temperature': 0},
                        {'reasoning_effort': 'high'}):
            envelope = {'config': options}
            with self.subTest(options=options):
                self.assertFalse(Draft202012Validator(engine.describe_config()).is_valid(envelope))
                with self.assertRaises(ValueError):
                    registry.validate_config(ProjectConfig(parameters={'engines': {'writer': envelope}}))
                with self.assertRaises(ValueError):
                    registry.validate_config(ProjectConfig(), session_config={'parameters': {'engines': {'writer': envelope}}})
                with self.assertRaises(ValidationError):
                    engine.for_agent({'engine': 'writer', 'engine_options': envelope})

    def test_agent_model_options_do_not_enter_engine_resolution(self):
        definition = {'engine': 'writer', 'completion': {'model': 'test/model', 'temperature': None},
                      'engine_options': {'policy': {'max_iterations': 2}}}
        original = deepcopy(definition)
        engine = LoopEngine().for_agent(definition)
        view = engine.resolve_config({'parameters': {'engines': {'writer': {'policy': {'max_iterations': 4}}}}}, 'writer')
        self.assertEqual(view['values'], {'policy': {'max_iterations': 2}})
        self.assertEqual(view['sources']['/policy/max_iterations'], 'agent')
        self.assertEqual(definition, original)


class AgentModelExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model = ScriptedCompletion(*[[chunk('answer', finish='stop')] for _ in range(8)])
        self.engine = LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=self.model))
        self.app = LargeLanguageModel(self.root, engines={'loop': self.engine})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(config={'parameters': {'engines': {'loop': {
            'policy': {'max_iterations': 3, 'request_timeout': 5}}}}})

    async def run_agent(self, agent_id):
        session = await self.project.sessions.acreate(agent_ids=[agent_id])
        return (await (await session.run.submit('request', recipients=[agent_id])).wait(timeout=15))[0]

    async def test_shared_engine_uses_only_selected_agent_instructions(self):
        definitions = [
            {'engine': 'loop', 'completion': {'model': 'openai/missing'}},
            {'engine': 'loop', 'completion': {'model': 'openai/null'}, 'system_prompt': None},
            {'engine': 'loop', 'completion': {'model': 'openai/empty'}, 'system_prompt': ''},
            {'engine': 'loop', 'completion': {'model': 'openai/first'}, 'system_prompt': 'first instructions'},
            {'engine': 'loop', 'completion': {'model': 'openai/second'}, 'system_prompt': 'second instructions'},
        ]
        for index, definition in enumerate(definitions):
            await self.project.agents.acreate(definition, identifier=f'agent-{index}')
        runs = await asyncio.gather(*(self.run_agent(f'agent-{index}') for index in range(len(definitions))))
        self.assertTrue(all(run.data.status == RunStatus.COMPLETED for run in runs), [run.data.error for run in runs])
        requests = {request['model']: request for request in self.model.requests}
        for index, definition in enumerate(definitions):
            request = requests[definition['completion']['model']]
            prompts = [message['content'] for message in request['messages'] if message['role'] == 'system']
            self.assertEqual(prompts, [definition['system_prompt']] if definition.get('system_prompt') else [])
            self.assertIn({'role': 'user', 'content': 'request'}, request['messages'])
            self.assertEqual(await self.project.agents.aload(f'agent-{index}'), definition)
        self.assertEqual(self.engine.resolve_config({}, 'loop')['values'], {})

    async def test_shared_engine_uses_independent_agent_models_and_exact_sdk_values(self):
        options = [
            {'model': 'openai/first', 'api_base': 'http://first.invalid/v1', 'api_key': 'first-test-key',
             'temperature': 0, 'reasoning_effort': 'low', 'timeout': None,
             'future_provider_option': {'items': [1]}},
            {'model': 'openai/second', 'api_base': 'http://second.invalid/v1', 'api_key': 'second-test-key',
             'temperature': 0.8, 'reasoning_effort': 'high', 'max_retries': 3},
        ]
        for name, params in zip(('first', 'second'), options):
            await self.project.agents.acreate({'engine': 'loop', 'completion': params}, identifier=name)
        runs = await asyncio.gather(self.run_agent('first'), self.run_agent('second'))
        self.assertTrue(all(r.data.status == RunStatus.COMPLETED for r in runs))
        by_model = {request['model']: request for request in self.model.requests}
        for params in options:
            sent = by_model[params['model']]
            for key, value in params.items():
                self.assertEqual(sent[key], value)
            if 'timeout' not in params:
                self.assertNotIn('timeout', sent)
            self.assertNotIn('max_iterations', sent)
            self.assertNotIn('request_timeout', sent)
        self.assertEqual(self.engine.resolve_config({}, 'loop')['values'], {})
        self.assertNotIn('completion', (await self.project.aget_data()).config.parameters['engines']['loop'].get('config', {}))

    async def test_missing_model_does_not_borrow_another_agents_model(self):
        await self.project.agents.acreate({'engine': 'loop', 'completion': {'model': 'openai/other'}}, identifier='other')
        await self.project.agents.acreate({'engine': 'loop'}, identifier='missing')
        run = await self.run_agent('missing')
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.model.requests, [])
        self.assertEqual(await self.project.agents.aload('missing'), {'engine': 'loop'})

    async def test_saved_agent_is_only_source_and_revision_changes_model(self):
        await self.project.agents.acreate({'engine': 'loop', 'completion': {'model': 'openai/first'}}, identifier='writer')
        first = await self.run_agent('writer')
        saved = await self.project.agents.asnapshot('writer')
        await self.project.agents.aupdate('writer', {'completion': {'model': 'openai/second'}}, expected_revision=saved['revision'])
        second = await self.run_agent('writer')
        self.assertEqual([r['model'] for r in self.model.requests], ['openai/first', 'openai/second'])
        self.assertEqual(first.data.metadata['agent_binding']['definition']['completion']['model'], 'openai/first')
        self.assertNotEqual(first.data.metadata['agent_binding']['revision'], second.data.metadata['agent_binding']['revision'])
