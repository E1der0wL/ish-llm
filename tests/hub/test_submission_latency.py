"""Input admission must not wait for the background history projection."""

import asyncio
from concurrent.futures import Future
from dataclasses import replace
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from hub.backend.runtime import HubConfig
from hub.backend.worker import BackendWorker
from hub.model import AgentSummary, ChatMessage, HubSnapshot, SessionSummary, SubmissionResult, RunSummary, RetrySummary
from hub.ui.live import LiveController, LiveHubView


class SubmissionLatencyTests(unittest.IsolatedAsyncioTestCase):
    def test_simultaneous_agent_clocks_advance_without_backend_reads(self):
        view = LiveHubView()
        messages = (
            ChatMessage('assistant', '', id='a', status='streaming', elapsed_seconds=2),
            ChatMessage('assistant', '', id='b', status='streaming', elapsed_seconds=1),
        )
        snapshot = HubSnapshot('p', 'Project', 'test/model', 'file',
            (SessionSummary('s', 'Session', 'running'),), 's', messages)
        try:
            with patch('hub.widget.feedback.preparing.monotonic', return_value=100.0) as clock:
                view.apply_snapshot(snapshot)
                clock.return_value = 101.0
                displayed = view.preparing.project(('p', 's'), messages)
            self.assertEqual([message.elapsed_seconds for message in displayed], [3, 2])
            self.assertEqual([message.elapsed_seconds for message in snapshot.messages], [2, 1])
        finally:
            view.close()

    async def test_retry_countdown_redraws_even_when_elapsed_label_is_unchanged(self):
        view = LiveHubView()
        view.apply_snapshot(HubSnapshot('p', 'Project', 'test/model', 'file',
            (SessionSummary('s', 'Session', 'idle'),), 's', (),
            run=RunSummary('r', 'running', 'loop', 0, True, (), '', RetrySummary(1, 4, 110.1))))
        view.visible = True
        preparing = view.preparing
        preparing.clocks.clear()
        preparing.pending[('p', 's')] = None
        preparing.started[('p', 's')] = 100.0
        try:
            with patch('hub.widget.feedback.preparing.monotonic', return_value=100.0) as clock, \
                    patch('hub.widget.feedback.retry_status.monotonic', new=clock):
                preparing._painted_clock = preparing._clock_label()
                times = iter((100.2,))
                async def tick(_):
                    clock.return_value = next(times)
                def painted():
                    preparing.pending.clear()
                with patch('hub.widget.feedback.preparing.asyncio.sleep', side_effect=tick) as sleep, \
                        patch.object(preparing, 'paint', side_effect=painted) as paint:
                    await preparing._tick()
                sleep.assert_called_once()
                self.assertAlmostEqual(sleep.call_args.args[0], .1)
                paint.assert_called_once_with()
        finally:
            view.close()

    async def test_clock_ticks_redraw_only_when_displayed_duration_changes(self):
        view = LiveHubView()
        view.apply_snapshot(HubSnapshot('p', 'Project', 'test/model', 'file',
            (SessionSummary('s', 'Session', 'idle'),), 's', ()))
        view.visible = True
        preparing = view.preparing
        preparing.pending[('p', 's')] = None
        preparing.started[('p', 's')] = 100.0
        try:
            with patch('hub.widget.feedback.preparing.monotonic', return_value=100.0) as clock:
                preparing._painted_clock = preparing._clock_label()
                times = iter((100.1, 100.2, 101.1))
                async def tick(_):
                    clock.return_value = next(times)
                def painted():
                    preparing.pending.clear()
                with patch('hub.widget.feedback.preparing.asyncio.sleep', side_effect=tick) as sleep, \
                        patch.object(preparing, 'paint', side_effect=painted) as paint:
                    await preparing._tick()
                self.assertEqual(sleep.call_count, 3)
                self.assertEqual(sleep.call_args_list[0].args[0], 1.0)
                paint.assert_called_once_with()
        finally:
            view.close()

    async def test_preparing_header_keeps_submitted_recipients_until_durable_response(self):
        view = LiveHubView()
        base = HubSnapshot('p', 'Project', 'test/model', 'file',
            (SessionSummary('s', 'Session', 'idle', participant_ids=('a', 'b')),), 's', (),
            agents=(AgentSummary('a', 'loop', title='검토자'), AgentSummary('b', 'loop', title='작성자')),
            participant_ids=('a', 'b'))
        view.apply_snapshot(base)
        view.visible = True
        try:
            view.recipient_id = 'b'
            view.preparing.start(('p', 's'), 'Request', recipients=('b',))
            self.assertEqual(view.output.transcript.control.messages[-1].author, '작성자')
            view.recipient_id = 'a'
            view.apply_snapshot(base)
            self.assertEqual(view.output.transcript.control.messages[-1].author, '작성자')
            view.preparing.accepted(('p', 's'), 'u')
            view.apply_snapshot(replace(base, messages=(ChatMessage('user', 'Request', id='u'),
                ChatMessage('assistant', 'Answer', id='answer', author='작성자', status='completed'))))
            self.assertEqual(view.output.transcript.control.messages[-1].author, '작성자')
            self.assertFalse(view.preparing.authors)
            view.preparing.start(('p', 's'), recipients=('a', 'b'))
            self.assertEqual(view.output.transcript.control.messages[-1].author, '검토자, 작성자')
            view.preparing.finish(('p', 's'))
            self.assertFalse(view.preparing.authors)
        finally:
            view.close()

    async def test_admission_preempts_blocked_background_snapshot_and_refresh_resumes(self):
        entered, cancelled, retry = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls, published = [], []

        async def snapshot():
            calls.append("snapshot")
            if len(calls) == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            retry.set()
            return "updated"

        async def submit_input(*args):
            self.assertTrue(cancelled.is_set())
            return "persisted"

        worker = BackendWorker.__new__(BackendWorker)
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), snapshot=snapshot, submit_input=submit_input)
        worker.runtime.dirty.set()
        worker._initialized = asyncio.Event()
        worker._initialized.set()
        worker._closed = False
        worker._restarting = False
        worker._failure = None
        worker._commands = set()
        worker._snapshot_task = None
        worker._output_interval = 0.1
        worker._visible = True
        worker._interval_changed = asyncio.Event()
        worker.publish = published.append
        worker.report_error = Mock()
        refresher = asyncio.create_task(worker._refresh())
        try:
            async with asyncio.timeout(2):
                await entered.wait()
                self.assertEqual(await worker._execute("submit_input", ()), "persisted")
                await retry.wait()
                while not published:
                    await asyncio.sleep(0)
            self.assertEqual(published, ["updated"])
            worker.report_error.assert_not_called()
        finally:
            refresher.cancel()
            await asyncio.gather(refresher, return_exceptions=True)
        self.assertIsNone(worker._snapshot_task)
        self.assertFalse(worker._commands)

    async def test_receipt_consumes_draft_and_displays_input_without_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            view = LiveHubView()
            controller = LiveController(view, HubConfig(directory, auto_title=False))
            controller.app = NS(invalidate=Mock())
            controller.loop = asyncio.get_running_loop()
            calls = []

            def call(operation, *args, **kwargs):
                future = Future()
                calls.append((operation, args, future))
                return future

            controller.worker = NS(call=call)
            base = HubSnapshot("p", "Project", "test/model", "file",
                (SessionSummary("s", "Session", "idle"), SessionSummary("t", "Other", "idle")),
                "s", (), engines=("loop",), agents=(AgentSummary('assistant', 'loop', title='도우미'),),
                participant_ids=('assistant',))
            view.apply_snapshot(base)
            view.visible = True
            message = ChatMessage("user", "hello", "2026-10-08T01:00:00+00:00",
                                  status="queued", id="m", author="User")
            try:
                view.composer.text = "hello"
                controller.submit("s", "hello")
                controller.submit("s", "hello")
                self.assertTrue(view.preparing.visible())
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0], "submit_input")
                # Both local preparation rows appear immediately. Durable
                # history still waits for the receipt and replaces the draft.
                self.assertEqual(view.composer.text, "")
                self.assertEqual([(m.role, m.status) for m in view.output.transcript.control.messages],
                                 [("user", "preparing"), ("assistant", "preparing")])
                self.assertEqual(view.output.transcript.control.messages[-1].author, '도우미')
                self.assertEqual(view.sessions[view.selected].messages, ())
                calls[0][2].set_result(SubmissionResult(message=message))
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, "")
                self.assertEqual([m.id for m in view.output.transcript.control.messages], ["m", "preparing:s"])
                view.apply_snapshot(base)  # A stale projection cannot erase the receipt.
                self.assertEqual([m.id for m in view.output.transcript.control.messages], ["m", "preparing:s"])
                committed = replace(message, status="committed")
                view.apply_snapshot(replace(base, messages=(committed, ChatMessage("assistant", "reply", id="a"))))
                self.assertEqual([m.id for m in view.output.transcript.control.messages], ["m", "a"])
                self.assertEqual(view.output.transcript.control.messages[0].status, "committed")
                self.assertFalse(view._submitted_messages)
                self.assertFalse(view.preparing.visible())

                # A new identical draft must survive the older acknowledgement.
                view.composer.text = "same"
                controller.submit("s", "same")
                self.assertEqual(view.composer.text, "")
                view.composer.text = "same"
                calls[-1][2].set_result(SubmissionResult(message=replace(message, text="same", id="same")))
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, "same")

                # Typing while admission runs must not lose the newer draft.
                view.composer.text = "second"
                controller.submit("s", "second")
                view.composer.text = "new draft"
                calls[-1][2].set_result(SubmissionResult(message=replace(message, text="second", id="m2")))
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, "new draft")

                # A response for another session updates only that session.
                view.composer.text = "third"
                controller.submit("s", "third")
                view.apply_snapshot(base, select_id="t")
                view.composer.text = "other draft"
                calls[-1][2].set_result(SubmissionResult(message=replace(message, text="third", id="m3")))
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, "other draft")
                self.assertEqual(view.output.transcript.control.messages, ())
                self.assertEqual(view._drafts["s"], "")
                self.assertIn("m3", [m.id for m in view.sessions[0].messages])

                # A project switch also consumes only the submitted old draft.
                view.apply_snapshot(base, select_id="s")
                view.composer.text = "fourth"
                controller.submit("s", "fourth")
                other = replace(base, project_id="p2", selected_id="u",
                                sessions=(SessionSummary("u", "Elsewhere", "idle"),))
                view.apply_snapshot(other)
                view.composer.text = "project draft"
                calls[-1][2].set_result(SubmissionResult(message=replace(message, text="fourth", id="m4")))
                await asyncio.sleep(0)
                self.assertEqual(view._drafts["s"], "")
                self.assertEqual(view.composer.text, "project draft")
                self.assertEqual(view.output.transcript.control.messages, ())
            finally:
                view.close()
                view.progress.close()

    async def test_failed_admission_keeps_draft_and_has_no_local_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            view = LiveHubView()
            controller = LiveController(view, HubConfig(directory, auto_title=False))
            controller.app = NS(invalidate=Mock())
            controller.loop = asyncio.get_running_loop()
            future = Future()
            controller.worker = NS(call=lambda *args: future)
            view.apply_snapshot(HubSnapshot("p", "Project", "test/model", "file",
                (SessionSummary("s", "Session", "idle"),), "s", (), engines=("loop",)))
            try:
                view.composer.text = "preserve me"
                controller.submit("s", view.composer.text)
                self.assertEqual(view.composer.text, "")
                future.set_exception(ValueError("admission rejected"))
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, "preserve me")
                self.assertEqual(view.output.transcript.control.messages, ())
                self.assertFalse(controller._submitting)
                self.assertFalse(controller._choosing_submission)
                self.assertIn(view.t("operation_failed"), view.notice)
                self.assertIn("admission rejected", view.operation_diagnostics.text("p", view.t))
                future = Future()
                view.composer.text = "failed with new draft"
                controller.submit("s", view.composer.text)
                view.composer.text = "newer text"
                future.set_exception(ValueError("second rejection"))
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, "newer text")
                view.composer.text = ""
                view.input_history.move(-1)
                self.assertEqual(view.composer.text, "failed with new draft")
            finally:
                view.close()
                view.progress.close()
