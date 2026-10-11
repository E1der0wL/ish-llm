"""Approval continues the saved tool call and never submits its answer as a prompt."""

import asyncio
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig, HubRuntime
from hub.model import ChatMessage, HubSnapshot, SessionSummary
from hub.ui.application import create_application
from hub.ui.live import LiveController, LiveHubView
from llm.core.interactions import InteractionOption, InteractionRequest
from llm.engines.loop import LoopEngine
from tests.hub.support import SizedOutput
from tests.hub.test_live import until
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class ApprovalContinuationTests(unittest.IsolatedAsyncioTestCase):
    def test_queued_and_repeated_resume_preserve_one_display_turn_with_steering(self):
        from hub.backend.continuations import continued_messages
        def record(identifier, role, run_id, parent=None):
            return NS(id=identifier, role=role, run_id=run_id, metadata={'resume': {'run_id': parent}} if parent else {})
        records = [record('u1', 'user', 'r1'), record('a1', 'assistant', 'r1'),
                   record('u2', 'user', 'r2', 'r1'), record('a2', 'assistant', 'r2'),
                   record('steer', 'user', 'r2'), record('u3', 'user', 'r3', 'r2'),
                   record('a3', 'assistant', 'r3'), record('queued', 'user', None, 'r3')]
        messages = tuple(ChatMessage(r.role, {'a1': 'Before', 'a2': 'Middle', 'a3': 'After'}.get(r.id, r.id),
                                     id=r.id, status='completed') for r in records)
        result = continued_messages(records, messages)
        self.assertEqual([m.id for m in result], ['u1', 'a1', 'steer'])
        self.assertEqual(result[1].text, 'Before\n\nMiddle\n\nAfter')

    async def test_deny_saves_answer_without_resuming_or_executing(self):
        provider = ScriptedCompletion([chunk(calls=[call('{"path":"denied.txt","content":"no"}',
            name='file_create')], finish='tool_calls')])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(Path(root) / 'workspace', file_root=root, model='test/model',
                auto_title=False, engine_factories={'loop': lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                await runtime.submit(sid, 'Create')
                await runtime.sessions[sid].run.wait_idle()
                question = (await runtime.snapshot()).questions[0]
                self.assertTrue(await runtime.answer_question(runtime.project.id, sid, question['run_id'],
                    question['request'], 'deny', None))
                self.assertEqual(len(provider.requests), 1)
                self.assertFalse(Path(root, 'denied.txt').exists())
                self.assertFalse((await runtime.snapshot()).questions)
            finally:
                await runtime.close()

    async def test_enter_approval_preserves_output_and_reuses_completion(self):
        provider = ScriptedCompletion(
            [chunk('I will create the file. '),
             chunk(calls=[call('{"path":"answer.txt","content":"approved"}', name='file_create')], finish='tool_calls')],
            [chunk('Created.', finish='stop')])
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(Path(root) / 'workspace', file_root=root,
                model='test/model', auto_title=False, engine_factories={'loop': lambda: LoopEngine(completion_fn=provider)}),
                input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_text('/new\r\r')
                await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text('Create the file\r')
                await until(lambda: view.question.item is not None)
                await until(lambda: not controller._submitting)
                original = next(m for m in view.sessions[view.selected].messages
                                if m.role == 'assistant' and m.id != 'interaction:' + view.question.item['request']['id'])
                self.assertIn('I will create', original.text)
                self.assertFalse(Path(root, 'answer.txt').exists())
                self.assertEqual(len(provider.requests), 1)
                pipe.send_text('1\r\r')
                await until(lambda: view._last_snapshot.run.status == 'completed')
                messages = view._last_snapshot.messages
                self.assertIsNone(view.question.item)
                self.assertEqual(view.composer.text, '')
                self.assertEqual([m.text for m in messages if m.role == 'user'], ['Create the file'])
                response = next(m for m in messages if m.role == 'assistant')
                self.assertEqual(response.id, original.id)
                self.assertIn('I will create the file.', response.text)
                self.assertIn('Created.', response.text)
                self.assertEqual(len(provider.requests), 2)
                self.assertTrue(any(m['role'] == 'tool' for m in provider.requests[1]['messages']))
                self.assertEqual([m['content'] for m in provider.requests[1]['messages'] if m['role'] == 'user'], ['Create the file'])
                self.assertEqual(Path(root, 'answer.txt').read_text(), 'approved')
                # Reopening the projection still uses durable resume links.
                fresh = await asyncio.wrap_future(controller.worker.call('snapshot'))
                self.assertEqual(fresh.messages, messages)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_answer_consumed_before_io_and_stale_question_not_reopened(self):
        with tempfile.TemporaryDirectory() as root:
            view = LiveHubView()
            controller = LiveController(view, HubConfig(root))
            controller.app = NS(invalidate=Mock())
            controller.loop = asyncio.get_running_loop()
            calls = []
            def call_backend(operation, *args):
                future = Future()
                calls.append((operation, future))
                return future
            controller.worker = NS(call=call_backend)
            request = InteractionRequest('Approve?', (InteractionOption('approve', 'Approve', effect='approve'),
                InteractionOption('deny', 'Deny', effect='deny'))).to_dict()
            base = HubSnapshot('p', 'Project', 'test/model', 'file', (SessionSummary('s', 'Session', 'idle'),), 's', ())
            waiting = replace(base, questions=({'run_id': 'r', 'live': False, 'request': request},))
            try:
                view.apply_snapshot(base)
                view.composer.text = 'ordinary draft'
                view.apply_snapshot(waiting)
                view.composer.text = '1'
                view.question.answer()
                self.assertEqual(view.composer.text, '')
                self.assertTrue(view.question.busy)
                view.question.answer()
                self.assertEqual(len(calls), 1)
                calls[0][1].set_result(True)
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, 'ordinary draft')
                view.apply_snapshot(waiting)
                self.assertIsNone(view.question.item)
                self.assertEqual(view.composer.text, 'ordinary draft')
                self.assertEqual([op for op, _ in calls], ['answer_question', 'snapshot'])
                updated = {**waiting.questions[0], 'request': {**request, 'revision': 2}}
                view.apply_snapshot(replace(waiting, questions=(updated,)))
                self.assertIsNotNone(view.question.item)
                self.assertEqual(view.question.identity[-1], 2)
            finally:
                view.output_renderers.close()
                view.progress.close()
