"""Hub consumes durable questions without inventing delivery or execution authority."""

import asyncio
import tempfile
import threading
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.interactions.question_values import QuestionTool
from hub.backend.interactions.questions import QuestionAnswers
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class DurableQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, provider):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        runtime = HubRuntime(HubConfig(directory.name, model='test/model', auto_title=False,
            postprocess=False, prepare_provider=False, engine_factories={
                'loop': lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
        self.addAsyncCleanup(runtime.close)
        await runtime.start()
        sid = await runtime.new_session(title='Questions')
        return runtime, sid

    async def action(self, runtime, sid, operation, **payload):
        return await runtime.question_action(runtime.project.id, sid, operation, payload)

    async def test_answer_storage_is_separate_and_live_delivery_is_idempotent(self):
        model = ScriptedCompletion(
            [chunk(calls=[call('{"question":"Which format?"}', name=QuestionTool.NONBLOCKING_NAME)], finish='tool_calls')],
            [chunk(calls=[call('{"question":"Another optional question?"}', name=QuestionTool.NONBLOCKING_NAME, call_id='q2')], finish='tool_calls')],
            [chunk('Final answer', finish='stop')])
        entered, release = threading.Event(), threading.Event()
        def provider(**kwargs):
            if len(model.requests) == 1:
                yield chunk('Independent work continues. ')
                entered.set()
                if not release.wait(30):
                    raise TimeoutError('Question test gate')
            yield from model(**kwargs)
        runtime, sid = await self.start(provider)
        try:
            await runtime.submit(sid, 'Create a report and ask about its format')
            self.assertTrue(await asyncio.to_thread(entered.wait, 15))
            run = (await runtime.sessions[sid].run.alist())[0]
            rows = await self.action(runtime, sid, 'list', run_ids=[run.id])
            item, = rows
            self.assertTrue(item['nonblocking'])
            self.assertEqual(item['run_status'], 'running')
            from hub.backend.observability.snapshot import SnapshotReader
            observation = await SnapshotReader().read_session(sid, runtime.sessions[sid])
            self.assertTrue(observation.pending_input)
            self.assertTrue(observation.runtime.active_runs)
            targets = await self.action(runtime, sid, 'targets', run_id=run.id)
            self.assertTrue(targets['targets'])
            response = await self.action(runtime, sid, 'save', run_id=run.id,
                request=item['request'], option_id='reply', value='Markdown')
            self.assertEqual(response, await self.action(runtime, sid, 'save', run_id=run.id,
                request=item['request'], option_id='reply', value='Markdown'))
            handle = await runtime.sessions[sid].run.aload(run.id)
            self.assertIsNone(await handle.get_delivery(item['request']['id']))
            self.assertEqual(len(model.requests), 1)
            observation = await SnapshotReader().read_session(sid, runtime.sessions[sid])
            self.assertFalse(observation.pending_input)
            choice = targets['targets'][0]
            payload = dict(run_id=run.id, request_id=item['request']['id'],
                           target_run_id=run.id, targets=[choice['target_id']])
            with self.assertRaises(ValueError):
                await self.action(runtime, sid, 'deliver', **{**payload, 'target_run_id': 'another-run'})
            with self.assertRaises(ValueError):
                await self.action(runtime, sid, 'deliver', **{**payload, 'targets': ['nonexistent-node']})
            first, second = await asyncio.gather(*(self.action(runtime, sid, 'deliver', **payload) for _ in range(2)))
            self.assertEqual(first, second)
            self.assertIsNotNone(await handle.get_delivery(item['request']['id']))
        finally:
            release.set()
        await runtime.sessions[sid].run.wait_idle()
        self.assertEqual(len(await runtime.sessions[sid].run.alist()), 1)
        self.assertIn('Markdown', str(model.requests[-1]['messages']))
        self.assertEqual((await handle.aget_data()).status, 'completed')

    async def test_finished_source_keeps_questions_but_never_implicitly_starts_another_run(self):
        model = ScriptedCompletion(
            [chunk(calls=[call('{"question":"Optional detail?"}', name=QuestionTool.NONBLOCKING_NAME)], finish='tool_calls')],
            [chunk('Done without that detail', finish='stop')])
        runtime, sid = await self.start(model)
        await runtime.submit(sid, 'Create a report')
        await runtime.sessions[sid].run.wait_idle()
        source = (await runtime.sessions[sid].run.alist())[0]
        # Reload public handles rather than depending on a live question Future.
        handle = await runtime.sessions[sid].run.aload(source.id)
        view, = await handle.aquestions()
        rows = await self.action(runtime, sid, 'list', run_ids=[source.id])
        self.assertEqual(rows[0]['run_status'], 'completed')
        await self.action(runtime, sid, 'save', run_id=source.id, request=view.request.to_dict(),
                          option_id='reply', value='Late answer')
        with self.assertRaises(ValueError):
            await self.action(runtime, sid, 'deliver', run_id=source.id,
                request_id=view.request.id, target_run_id=source.id, targets=['root'])
        other = await runtime.new_session(title='Other')
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.action(runtime, other, 'save', run_id=source.id,
                request=view.request.to_dict(), option_id='reply', value='Wrong session')
        self.assertIsNone(await handle.get_delivery(view.request.id))
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(len(await runtime.sessions[sid].run.alist()), 1)
        self.assertEqual((await QuestionAnswers.observe(handle))[0]['response']['value'], 'Late answer')

    async def test_native_answer_cancel_race_has_one_durable_outcome(self):
        model = ScriptedCompletion(
            [chunk(calls=[call('{"question":"Optional?"}', name=QuestionTool.NONBLOCKING_NAME)], finish='tool_calls')],
            [chunk('Done', finish='stop')])
        runtime, sid = await self.start(model)
        await runtime.submit(sid, 'Create a report')
        await runtime.sessions[sid].run.wait_idle()
        source = (await runtime.sessions[sid].run.alist())[0]
        handle = await runtime.sessions[sid].run.aload(source.id)
        view, = await handle.aquestions()
        results = await asyncio.gather(
            QuestionAnswers.save(handle, view.request, 'reply', 'Answer'),
            handle.acancel_question(view.request), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        stored, = await handle.aquestions()
        self.assertIn(stored.status, ('answered', 'cancelled'))
        self.assertEqual(stored.response is not None, stored.status == 'answered')
        self.assertIsNone(await handle.get_delivery(view.request.id))
        self.assertEqual(len(model.requests), 2)
