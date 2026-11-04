"""Public question lifecycle with explicit delivery, never implicit resume or authority."""

import asyncio
import unittest
import threading
from dataclasses import replace
from datetime import datetime, timezone, timedelta

from llm.components.tools import QuestionHandler, QuestionMode
from llm.core.interactions import InteractionRequest, InteractionOption, approval_request
from tests.llm import test_long_running
from tests.llm.test_loop import chunk, call


class QuestionTests(unittest.IsolatedAsyncioTestCase):
    setup_app = test_long_running.LongRunningTests.setup_app

    @staticmethod
    def question(arguments):
        return InteractionRequest('Which file?', (InteractionOption('answer', 'Answer'),),
                                  kind='input', category='input.file', input_schema={'type': 'string'})

    async def start(self, mode, *extra):
        handler = QuestionHandler(self.question, mode=mode, revision='test-1')
        app, session, model = await self.setup_app(handler, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
            [chunk('done', finish='stop')], *extra])
        run = (await (await session.run.submit('go', recipients=['loop'])).wait())[0]
        return app, session, model, run

    async def test_nonblocking_question_outlives_completed_run(self):
        app, session, model, run = await self.start(QuestionMode.NONBLOCKING,
                                                   [chunk('received', finish='stop')])
        self.assertEqual(run.data.status, 'completed', run.data.error)
        views = await run.aquestions()
        self.assertEqual(len(views), 1)
        self.assertTrue(views[0].can_respond)
        self.assertFalse(views[0].can_resume)
        self.assertEqual(views[0].source_run_id, run.id)
        self.assertEqual((await run.steps.aload(views[0].source_step_id)).kind, 'tool')
        self.assertEqual(await run.ainteractions(), [])
        request = views[0].request
        response = await run.aanswer_question(request.respond('answer', value='file.py'))
        self.assertEqual(await run.aanswer_question(request.respond('answer', value='file.py')), response)
        self.assertIsNone(await run.get_delivery(request.id))
        self.assertEqual(len(model.requests), 2)
        delivered = await run.deliver_answer(request.id, 'The file is file.py', mode='submit', recipients=['loop'])
        repeated = await run.deliver_answer(request.id, 'The file is file.py', mode='submit', recipients=['loop'])
        self.assertEqual(delivered.id, repeated.id)
        result = (await delivered.wait())[0]
        self.assertEqual(result.data.status, 'completed', result.data.error)
        self.assertEqual(len(model.requests), 3)
        self.assertIsNotNone(await run.get_delivery(request.id))
        self.assertEqual((await run.aquestions())[0].status, 'answered')
        self.assertEqual(run.data.status, 'completed')

    async def test_blocking_mode_uses_existing_explicit_resume(self):
        app, session, model, run = await self.start(QuestionMode.BLOCKING)
        self.assertEqual(run.data.status, 'paused', run.data.error)
        self.assertEqual(await run.aquestions(), [])
        request = (await run.ainteractions())[0]
        await run.arespond(request.respond('answer', value='answer.py'))
        self.assertEqual(len(model.requests), 1)
        resumed = (await (await session.run.resume(run.id, agent_id='loop')).wait())[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertIn('answer.py', str(model.requests[-1]))

    async def test_cancel_and_answer_are_serialized_and_no_default_answer(self):
        _, session, model, run = await self.start(QuestionMode.NONBLOCKING)
        request = (await run.aquestions())[0].request
        await run.acancel_question(request)
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            await run.aanswer_question(request.respond('answer', value='late'))
        with self.assertRaisesRegex(ValueError, 'answered'):
            await run.deliver_answer(request.id, 'late', mode='submit', recipients=['loop'])
        self.assertEqual(len(model.requests), 2)

    async def test_question_cannot_be_an_approval_or_change_answer(self):
        from llm.core.questions import validate_question
        with self.assertRaisesRegex(ValueError, 'approval'):
            validate_question(approval_request('execute?'))
        _, _, _, run = await self.start(QuestionMode.NONBLOCKING)
        request = (await run.aquestions())[0].request
        with self.assertRaises(Exception):
            request.respond('answer', value=3)
        await run.aanswer_question(request.respond('answer', value='first'))
        with self.assertRaisesRegex(ValueError, 'differently'):
            await run.aanswer_question(request.respond('answer', value='second'))
        with self.assertRaisesRegex(ValueError, 'immutable'):
            await run.acancel_question(request)

    async def test_finished_steering_target_has_no_automatic_submit_fallback(self):
        _, _, model, run = await self.start(QuestionMode.NONBLOCKING)
        request = (await run.aquestions())[0].request
        await run.aanswer_question(request.respond('answer', value='late'))
        with self.assertRaises(ValueError):
            await run.deliver_answer(request.id, 'late', mode='steer', run_id=run.id)
        self.assertIsNone(await run.get_delivery(request.id))
        self.assertEqual(len(model.requests), 2)

    async def test_answer_during_execution_is_delivered_only_by_explicit_steer(self):
        from llm.providers.litellm import LiteLLMCompletionAdapter
        handler = QuestionHandler(self.question, mode='nonblocking', revision='1')
        app, session, model = await self.setup_app(handler, [
            [chunk(calls=[call('{}', name='act', call_id='q1')], finish='tool_calls')],
            [chunk(calls=[call('{}', name='act', call_id='q2')], finish='tool_calls')],
            [chunk('done', finish='stop')]])
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def completion(**kwargs):
            if len(model.requests) == 1:
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('test gate')
            yield from model(**kwargs)
        app.engines.get('loop').completion_adapter = LiteLLMCompletionAdapter(completion_fn=completion)
        pending = await session.run.submit('go', recipients=['loop'])
        self.assertTrue(await asyncio.to_thread(entered.wait, 10))
        run = (await session.run.alist())[0]
        run = await session.run.aload(run.id)
        request = (await run.aquestions())[0].request
        self.assertEqual(run.data.status, 'running')
        await run.aanswer_question(request.respond('answer', value='live.py'))
        self.assertIsNone(await run.get_delivery(request.id))
        first, second = await asyncio.gather(
            run.deliver_answer(request.id, 'Use live.py', mode='steer', run_id=run.id),
            run.deliver_answer(request.id, 'Use live.py', mode='steer', run_id=run.id))
        self.assertEqual(first.id, second.id)
        release.set()
        done = (await pending.wait())[0]
        self.assertEqual(done.data.status, 'completed', done.data.error)
        contents = [m.get('content') for m in model.requests[-1]['messages']]
        self.assertEqual(contents.count('Use live.py'), 1)
        # The existing steering contract defers not-yet-started Tools, including
        # a second question. The accepted answer never implicitly asks again.
        self.assertEqual([view.status for view in await run.aquestions()], ['answered'])

    async def test_question_and_answer_survive_reopen_without_auto_delivery(self):
        from llm.llm import LargeLanguageModel
        app, session, model, run = await self.start('nonblocking', [chunk('received', finish='stop')])
        request = (await run.aquestions())[0].request
        response = await run.aanswer_question(request.respond('answer', value='persisted.py'))
        root = session.project.paths.root.parent.parent
        project_id, session_id, run_id = session.project.id, session.id, run.id
        engine = app.engines.get('loop')
        component = app.project_manager.components.get('tools')
        await app.shutdown()
        reopened = LargeLanguageModel(root, engines={'loop': engine}, components=[component])
        self.addAsyncCleanup(reopened.shutdown)
        project = await reopened.projects.aload(project_id)
        session = await project.sessions.aload(session_id)
        run = await session.run.aload(run_id)
        self.assertEqual((await run.aquestions())[0].response, response)
        self.assertIsNone(await run.get_delivery(request.id))
        self.assertEqual(len(model.requests), 2)
        delivered = await run.deliver_answer(request.id, 'persisted.py', mode='submit', recipients=['loop'])
        self.assertEqual((await delivered.wait())[0].data.status, 'completed')

    async def test_answer_cancel_race_has_one_winner(self):
        from llm.core.interactions import InteractionResponse
        _, _, model, run = await self.start('nonblocking')
        request = (await run.aquestions())[0].request
        results = await asyncio.gather(run.aanswer_question(request.respond('answer', value='x')),
                                       run.acancel_question(request), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, Exception) for result in results), 1)
        view = (await run.aquestions())[0]
        self.assertIn(view.status, ('answered', 'cancelled'))
        self.assertEqual(isinstance(results[0], InteractionResponse), view.status == 'answered')
        self.assertEqual(len(model.requests), 2)

    async def test_missing_question_record_is_integrity_error(self):
        from llm.services.runtime.interactions import InteractionRepository
        _, _, _, run = await self.start('nonblocking')
        request = (await run.aquestions())[0].request
        InteractionRepository()._question_path(run.data, request.id).unlink()
        with self.assertRaisesRegex(ValueError, 'manifest'):
            await run.aquestions()

    async def test_orphan_question_cannot_be_answered_by_identifier(self):
        from llm.core.questions import QuestionPublication
        app, _, _, run = await self.start('nonblocking')
        request = (await run.aquestions())[0].request
        stored = run.data
        stored.metadata.pop(QuestionPublication.METADATA)
        app.run_repository.save(stored)
        with self.assertRaisesRegex(ValueError, 'manifest'):
            await run.aanswer_question(request.respond('answer', value='x'))

    async def test_deleted_source_turn_cannot_deliver_late_answer(self):
        app, session, model, run = await self.start('nonblocking')
        request = (await run.aquestions())[0].request
        await run.aanswer_question(request.respond('answer', value='x'))
        await app._manager(session._snapshot).shutdown()
        await session.adelete_turn(run.data.input_message_id)
        with self.assertRaisesRegex(ValueError, 'deleted'):
            await run.deliver_answer(request.id, 'x', mode='submit', recipients=['loop'])
        self.assertEqual(len(model.requests), 2)

    async def test_expired_question_never_synthesizes_an_answer(self):
        handler = QuestionHandler(lambda arguments: replace(self.question(arguments),
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()),
            mode='nonblocking', revision='1')
        _, session, model = await self.setup_app(handler, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]])
        run = (await (await session.run.submit('go', recipients=['loop'])).wait())[0]
        self.assertEqual(run.data.status, 'completed', run.data.error)
        view = (await run.aquestions())[0]
        self.assertEqual(view.status, 'expired')
        self.assertIsNone(view.response)
        self.assertFalse(view.can_respond)
        with self.assertRaises(ValueError):
            view.request.respond('answer', value='late')


class GraphQuestionTests(unittest.IsolatedAsyncioTestCase):
    from tests.llm import test_nested_graph as fixture
    asyncSetUp = fixture.NestedGraphTests.asyncSetUp
    setup = fixture.NestedGraphTests.setup
    request = fixture.NestedGraphTests.request

    async def test_direct_graph_tool_blocking_question(self):
        from llm.engines.graph.tool import ToolNode
        from llm.components.tools import Tool, ToolRegistry
        from tests.llm.support.runtime_tools import TEST_CONTRACT
        handler = QuestionHandler(QuestionTests.question, mode='blocking', revision='1')
        self.tools = ToolRegistry([Tool('effect', 'question', {'type': 'object'}, handler, contract=TEST_CONTRACT)])
        await self.setup({'main': self.fixture.action_graph('tool', tool='effect')}, handlers={'tool': ToolNode()})
        run = await self.request()
        self.assertEqual(run.data.status, 'paused', run.data.error)
        request = (await run.ainteractions())[0]
        await run.arespond(request.respond('answer', value='graph.py'))
        done = (await (await self.session.run.resume(run.id, agent_id='main-agent')).wait())[0]
        self.assertEqual(done.data.status, 'completed', done.data.error)

    async def test_nested_agent_nonblocking_question_keeps_source_step(self):
        from llm.engines.graph.agent import AgentNode
        from llm.engines.loop import LoopEngine
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from llm.components.tools import Tool, ToolRegistry
        from tests.llm.support.runtime_tools import TEST_CONTRACT
        from tests.llm.test_loop import ScriptedCompletion
        handler = QuestionHandler(QuestionTests.question, mode='nonblocking', revision='1')
        self.tools = ToolRegistry([Tool('effect', 'question', {'type': 'object'}, handler, contract=TEST_CONTRACT)])
        model = ScriptedCompletion([chunk(calls=[call('{}', name='effect')], finish='tool_calls')],
                                   [chunk('continuing without an answer', finish='stop')])
        agent = AgentNode(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        await self.setup({'main': self.fixture.action_graph('agent', agent='worker')}, handlers={'agent': agent})
        await self.agents.acreate({'engine': 'loop', 'completion': {'model': 'test'}, 'tools': ['effect']}, identifier='worker')
        run = await self.request()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        view, = await run.aquestions()
        step = await run.steps.aload(view.source_step_id)
        self.assertEqual(step.kind, 'tool')
        self.assertEqual(view.source_run_id, run.id)
        self.assertEqual(view.status, 'pending')
        await run.aanswer_question(view.request.respond('answer', value='nested.py'))
        self.assertEqual(len(model.requests), 2)
        self.assertIsNone(await run.get_delivery(view.request.id))
