"""Summary-model-only saves use Memory/Data public APIs and preserve user policies."""

from copy import deepcopy
import json
import tempfile
import unittest

from hub.backend.runtime import HubRuntime, HubConfig
from hub.backend.project.memory_setup import MemorySetup
from hub.config.defaults import HubDefaults
from llm.components.data import DataComponent
from llm.components.memory import MemoryComponent
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.test_large_data import SourceTools, answer
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class MemorySetupTests(unittest.TestCase):
    def prepare(self, parameters, components=('memory',)):
        config = {'parameters': {'components': {'memory': parameters}}}
        before = deepcopy(config)
        prepared, selected = MemorySetup.prepare(config, components)
        self.assertEqual(config, before)
        return prepared.parameter_targets.component('memory').to_dict(), selected

    def test_no_model_does_not_enable_summary_or_choose_credentials(self):
        for completion in ({}, {'model': ''}, None):
            value = {'config': {'processing': {'completion': completion}}}
            prepared, selected = self.prepare(value)
            self.assertEqual(prepared, value)
            self.assertEqual(selected, ['memory'])

    def test_model_enables_defaults_and_preserves_explicit_false_limits_and_connection(self):
        connection = {'model': 'private/summary', 'api_base': 'https://test.invalid'}
        value = {'config': {'processing': {'completion': connection}},
                 'policy': {'processing': {'summarize': False, 'max_summary_calls': 3}}}
        prepared, selected = self.prepare(value)
        self.assertEqual(prepared['config']['processing']['completion'], connection)
        self.assertEqual(prepared['config']['processing']['tool_result_mode'], 'summary')
        self.assertFalse(prepared['policy']['processing']['summarize'])
        self.assertEqual(prepared['policy']['processing']['max_summary_calls'], 3)
        self.assertEqual(selected, ['memory', 'data'])
        self.assertEqual(self.prepare(prepared), (prepared, selected))
        self.assertEqual(self.prepare(value, ())[0], value)

    def test_explicit_preview_or_destination_is_not_overwritten(self):
        value = {'config': {'processing': {'completion': {'model': 'test/summary'}}},
                 'policy': {'processing': {'compress_tools': True}}}
        prepared, selected = self.prepare(value)
        self.assertNotIn('tool_result_mode', prepared['config']['processing'])
        self.assertEqual(selected, ['memory'])
        value['policy']['processing']['compress_tools'] = False
        value['config']['processing'].update(tool_result_mode='reference',
            tool_data={'component': 'custom_files', 'identifier': 'explicit'})
        prepared, selected = self.prepare(value)
        self.assertEqual(prepared['config']['processing']['tool_data'],
                         value['config']['processing']['tool_data'])
        self.assertEqual(selected, ['memory'])


class MemorySetupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_only_summary_model_chunks_large_tool_output_and_preserves_source(self):
        source = SourceTools()
        source.content = 'Unique raw result ' * 4000
        summaries = []
        def auxiliary(**request):
            summaries.append(json.loads(request['messages'][1]['content']))
            yield from answer('{"summary":"Important summarized result"}')
        model = ScriptedCompletion(
            [chunk(calls=[call('{}', name='source_read')], finish='tool_calls')], answer())
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, model='test/main', auto_title=False,
                engine_factories={'loop': lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},
                component_factories=(lambda: MemoryComponent(completion_adapter=
                    LiteLLMCompletionAdapter(completion_fn=auxiliary)), DataComponent, lambda: source)))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            settings = runtime.settings_service
            record = await settings.load_project(runtime.project.id)
            original = deepcopy(record['values']['config'])
            self.assertEqual((await settings.load_project(runtime.project.id))['values']['config'], original)
            values = deepcopy(record['values'])
            values['components'].remove('data')  # Explicit save enables the required public dependency.
            values['config']['parameters']['components']['memory']['config']['processing'][
                'completion'] = {'model': 'test/summary'}
            saved = await settings.save_project(runtime.project.id, values, record['config_version'])
            self.assertIn('data', saved['values']['components'])
            files = await runtime.project.components.aget('data')
            self.assertIn(HubDefaults.MEMORY_SOURCE, await files.alist())
            snapshot = await files.asnapshot(HubDefaults.MEMORY_SOURCE)
            await settings.save_project(runtime.project.id, saved['values'], saved['config_version'])
            self.assertEqual(await files.asnapshot(HubDefaults.MEMORY_SOURCE), snapshot)
            sid = await runtime.new_session()
            session = runtime.sessions[sid]
            run = (await (await session.run.submit('Summarize the command result',
                recipients=['assistant'])).wait(timeout=20))[0]
            self.assertEqual(run.data.status, 'completed', run.data.error)
            self.assertEqual(source.calls, 1)
            self.assertGreater(len(summaries), 1)
            self.assertLess(max(len(json.dumps(part)) for part in summaries), len(source.content))
            memory = await runtime.project.components.aget('memory')
            saved_result, = await memory.alist_tool_summaries(sid)
            self.assertEqual(saved_result['request']['status'], 'applied')
            reference = saved_result['request']['reference']
            raw = await memory.aread_source(sid, saved_result['request']['id'],
                expected_revision=reference['revision'], offset=0, limit=100)
            self.assertIn('Unique raw result', raw['text'])
            final_tool = [message for message in model.requests[-1]['messages'] if message['role'] == 'tool']
            self.assertTrue(final_tool)
            self.assertNotIn(source.content, str(final_tool))
            self.assertIn('Important summarized result', str(final_tool))
