"""Hub bootstrap and public llm handles are sufficient; no private backend storage access."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import tempfile
import unittest

from hub.backend.runtime import HubRuntime, HubConfig
from llm.components.data import DataComponent
from llm.components.memory import MemoryComponent
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm.test_large_data import SourceTools, answer
from tests.llm.configuration_fixtures import memory_settings


class SummaryAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_can_configure_request_cancel_resume_and_read_sources_via_public_api(self):
        with tempfile.TemporaryDirectory() as directory:
            auxiliary_calls = []
            def auxiliary(**request):
                auxiliary_calls.append(request)
                yield from answer('{"summary":"Hub summary"}')
            model = ScriptedCompletion(answer(), answer(), answer(), answer(),
                [chunk(calls=[call('{}', name='source_read')], finish='tool_calls')], answer())
            runtime = HubRuntime(HubConfig(directory, model='test/main', auto_title=False,
                engine_factories={'loop': lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},
                component_factories=(lambda: MemoryComponent(completion_adapter=LiteLLMCompletionAdapter(completion_fn=auxiliary)), DataComponent, SourceTools)))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            identifier = await runtime.new_session()
            session = runtime.sessions[identifier]
            memory = await runtime.project.components.aget('memory')
            files = await runtime.project.components.aget('data')
            await memory.aconfigure({})
            for prompt in ('first source ' * 70, 'recent'):
                run = await (await session.run.submit(prompt, engine='loop')).wait(timeout=20)
                self.assertEqual(run.data.status, 'completed', run.data.error)
            options = {'method': 'summary', 'keep_turns': 0, 'summary_chars': 40,
                'model_input_chars': 300, 'max_summary_calls': 1, 'prompt': 'Summarize',
                'completion': {'model': 'test/summary'}}
            schema = memory.describe_compaction()
            self.assertIn('model_input_chars', schema['required'])
            view = await memory.aget_context(identifier)
            await memory.arequest_compaction(identifier, options, expected_revision=view['revision'])
            view = await memory.aget_context(identifier)
            cancelled = await memory.acancel_compaction(identifier, expected_revision=view['revision'])
            self.assertEqual(cancelled['request']['status'], 'cancelled')
            await memory.arequest_compaction(identifier, options, expected_revision=cancelled['revision'])
            await (await session.run.submit('compact', engine='loop')).wait(timeout=20)
            view = await memory.aget_context(identifier)
            self.assertEqual(view['request']['status'], 'partial')
            offset = view['request']['checkpoint']['offset']
            await memory.aresume_compaction(identifier, expected_revision=view['revision'])
            await (await session.run.submit('continue', engine='loop')).wait(timeout=20)
            view = await memory.aget_context(identifier)
            self.assertGreater(view['request']['checkpoint']['offset'], offset)
            await memory.acancel_compaction(identifier, expected_revision=view['revision'])
            await files.acreate({}, identifier='results')
            await memory.aconfigure(memory_settings({'processing': {'priority': 0, 'tool_result_mode': 'reference',
                'tool_data': {'component': 'data', 'identifier': 'results'}, 'tool_result_chars': 100}}))
            run = await (await session.run.submit('read source', engine='loop')).wait(timeout=20)
            self.assertEqual(run.data.status, 'completed', run.data.error)
            saved = (await memory.alist_tool_summaries(identifier))[0]
            reference = saved['request']['reference']
            source = await memory.aread_source(identifier, saved['request']['id'],
                expected_revision=reference['revision'], offset=0, limit=30)
            self.assertIn('한글 source', source['text'])
            self.assertEqual(len(auxiliary_calls), 2)
            await memory.adelete_tool_summary(identifier, saved['request']['id'], expected_revision=saved['revision'])
            snapshot = await files.asnapshot('results')
            await files.adelete_file('results', reference['path'], expected_revision=snapshot['version'])
