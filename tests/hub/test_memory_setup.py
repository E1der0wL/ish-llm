"""Summary-model-only saves use Memory/Data public APIs and preserve user policies."""

from copy import deepcopy
import asyncio
import json
import tempfile
import threading
import unittest

from hub.backend.runtime import HubRuntime, HubConfig
from hub.backend.project.memory_setup import MemorySetup
from hub.config.defaults import HubDefaults
from hub.ui.presentation import present_message
from hub.widget.transcript.thought_trace import render_trace_item
from hub.config.theme import HubTheme
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
        self.assertEqual(prepared['policy']['processing']['model_input_chars'], 18000)
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

    def test_summary_leaves_wrapper_room_and_preserves_explicit_active_policy(self):
        value = {'config': {'processing': {'completion': {'model': 'test/summary'}}},
                 'policy': {'processing': {'context_chars': 4000, 'compact_active': False,
                                           'active_keep_iterations': 3, 'keep_turns': 5}}}
        prepared, _ = self.prepare(value)
        self.assertEqual(prepared['config']['processing']['summary_chars'], 2000)
        self.assertFalse(prepared['policy']['processing']['compact_active'])
        self.assertEqual(prepared['policy']['processing']['active_keep_iterations'], 3)
        self.assertEqual(prepared['policy']['processing']['keep_turns'], 5)
        value['config']['processing']['summary_chars'] = 3000
        prepared, _ = self.prepare(value)
        self.assertEqual(prepared['config']['processing']['summary_chars'], 3000)


class MemorySetupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_tool_summary_uses_verified_source_without_provider_overflow(self):
        from hub.backend.execution.retries import HubLoopEngine
        from hub.backend.execution.tool_references import ToolReferences
        source = SourceTools()
        source.content = 'Preserved command evidence\n' * 50000
        summaries, requests = [], []
        def auxiliary(**request):
            summaries.append(request)
            yield from answer('{"summary":"Partial summary of the first fragment"}')
        def main(**request):
            requests.append(request)
            if len(requests) == 1:
                yield chunk(calls=[call('{}', name='source_read')], finish='tool_calls')
                return
            # The server rejects an overlong request. Hub must act BEFORE this boundary.
            if len(json.dumps(request['messages'])) > 100000:
                raise AssertionError('HTTP 400: maximum context length is 204800 tokens')
            result = json.loads(next(m['content'] for m in request['messages'] if m['role'] == 'tool'))
            self.assertEqual(result['notice'], ToolReferences.NOTICE)
            self.assertEqual(result['original_chars'], len(source.content))
            self.assertEqual(result['tool'], 'memory_tool_result')
            self.assertNotIn('Partial summary', json.dumps(result))
            yield from answer('Source retained; read the requested range for further analysis.')
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/main', auto_title=False,
                project_config={'parameters':{'engines':{'loop':{'policy':{'final_review':None}}}}},
                engine_factories={'loop':lambda: HubLoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=main))},
                component_factories=(lambda: MemoryComponent(completion_adapter=
                    LiteLLMCompletionAdapter(completion_fn=auxiliary)), DataComponent, lambda: source)))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            service = runtime.settings_service
            record = await service.load_project(runtime.project.id)
            values = deepcopy(record['values'])
            memory = values['config']['parameters']['components']['memory']
            memory['config']['processing']['completion'] = {'model':'test/summary'}
            memory['policy']['processing']['max_summary_calls'] = 1
            await service.save_project(runtime.project.id, values, record['config_version'])
            sid = await runtime.new_session()
            run, = await (await runtime.sessions[sid].run.submit(
                'Analyze the command result', recipients=['assistant'])).wait(timeout=30)
            self.assertEqual(run.data.status, 'completed', run.data.error)
            self.assertEqual((len(requests), len(summaries), source.calls), (2, 1, 1))
            self.assertFalse(any(step.metadata.get('hub_recovery') for step in await run.steps.alist()))
            data = await runtime.project.components.aget('memory')
            state, = await data.alist_tool_summaries(sid)
            self.assertEqual(state['request']['status'], 'partial')
            raw = await data.aread_source(sid, state['request']['id'],
                expected_revision=state['request']['reference']['revision'], offset=0, limit=None)
            self.assertEqual(raw['text'], source.content)

    async def test_accumulated_small_tool_results_are_compacted_before_next_provider_call(self):
        source = SourceTools()
        source.content = 'result ' * 1000  # Each result is below tool_result_chars.
        summaries, sizes = [], []
        def auxiliary(**request):
            summaries.append(request)
            yield from answer('{"summary":"Verified earlier results; continue the remaining work."}')
        scripted = ScriptedCompletion(*[
            [chunk(calls=[call('{}', name='source_read', call_id=f'c{i}')], finish='tool_calls')]
            for i in range(12)
        ], answer())
        def main(**request):
            size = len(json.dumps(request['messages']))
            sizes.append(size)
            if size > 50000:
                raise AssertionError('Provider context exceeded after accumulated Tool results')
            yield from scripted(**request)
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, model='test/main', auto_title=False,
                engine_factories={'loop': lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=main))},
                component_factories=(lambda: MemoryComponent(completion_adapter=
                    LiteLLMCompletionAdapter(completion_fn=auxiliary)), DataComponent, lambda: source)))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            service = runtime.settings_service
            record = await service.load_project(runtime.project.id)
            values = deepcopy(record['values'])
            values['config']['parameters']['components']['memory']['config']['processing'][
                'completion'] = {'model': 'test/summary'}
            saved = await service.save_project(runtime.project.id, values, record['config_version'])
            memory_config = saved['values']['config']['parameters']['components']['memory']
            self.assertTrue(memory_config['policy']['processing']['compact_active'])
            self.assertEqual(memory_config['policy']['processing']['keep_turns'], 1)
            self.assertLess(memory_config['config']['processing']['summary_chars'],
                            memory_config['policy']['processing']['context_chars'])
            sid = await runtime.new_session()
            run, = await (await runtime.sessions[sid].run.submit(
                'Read each result and finish the twelve-step task', recipients=['assistant'])).wait(timeout=40)
            self.assertEqual(run.data.status, 'completed', run.data.error)
            self.assertEqual(source.calls, 12)
            self.assertTrue(summaries)
            self.assertEqual(len(scripted.requests), 13)
            self.assertLess(max(sizes), len(source.content) * 12)
            self.assertIn('Verified earlier results', str(scripted.requests[-1]['messages']))

    async def test_save_only_summary_model_chunks_large_tool_output_and_preserves_source(self):
        source = SourceTools()
        source.content = 'Unique raw result ' * 4000
        summaries = []
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def auxiliary(**request):
            summaries.append(json.loads(request['messages'][1]['content']))
            if len(summaries) == 1:
                yield {'choices': [{'index': 0, 'delta': {'reasoning_content': 'Internal recovery work'}, 'finish_reason': None}]}
                entered.set()
                if not release.wait(15):
                    raise TimeoutError('Summary display was not observed')
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
            request = await session.run.submit('Summarize the command result', recipients=['assistant'])
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                async with asyncio.timeout(10):
                    while True:
                        snapshot = await runtime.snapshot()
                        thoughts = [m for m in snapshot.messages if m.role == 'reasoning' and m.phase == 'summarizing']
                        if thoughts:
                            break
                        await asyncio.sleep(.01)
                shown = present_message(thoughts[-1], runtime.t)
                rendered = ''.join(f[1] for item in shown.trace
                    for row in render_trace_item(item, 80, HubTheme(), runtime.t) for f in row)
                self.assertIn('컨텍스트 압축', rendered)
                self.assertNotIn('Internal recovery work', rendered + shown.text)
            finally:
                release.set()
            run = (await request.wait(timeout=20))[0]
            self.assertEqual(run.data.status, 'completed', run.data.error)
            self.assertEqual(source.calls, 1)
            self.assertGreater(len(summaries), 1)
            processing = saved['values']['config']['parameters']['components']['memory']['policy']['processing']
            self.assertLessEqual(max(len(json.dumps(part, ensure_ascii=False)) for part in summaries),
                                 processing['model_input_chars'])
            self.assertEqual(''.join(message['content'] for part in summaries for message in part['messages']), source.content)
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
