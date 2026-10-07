"""Input admission must not wait for the background history projection."""

import asyncio
from concurrent.futures import Future
from dataclasses import replace
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from hub.backend.runtime import HubConfig
from hub.backend.worker import BackendWorker
from hub.model import ChatMessage, HubSnapshot, SessionSummary, SubmissionResult
from hub.ui.live import LiveController, LiveHubView


class SubmissionLatencyTests(unittest.IsolatedAsyncioTestCase):
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
        worker._failure = None
        worker._commands = set()
        worker._snapshot_task = None
        worker._output_interval = 0.1
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

            def call(operation, *args):
                future = Future()
                calls.append((operation, args, future))
                return future

            controller.worker = NS(call=call)
            base = HubSnapshot("p", "Project", "test/model", "file",
                (SessionSummary("s", "Session", "idle"), SessionSummary("t", "Other", "idle")),
                "s", (), engines=("loop",))
            view.apply_snapshot(base)
            view.visible = True
            message = ChatMessage("user", "hello", "2026-10-08T01:00:00+00:00",
                                  status="queued", id="m", author="User")
            try:
                view.composer.text = "hello"
                controller.submit("s", "hello")
                controller.submit("s", "hello")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0], "submit_input")
                # No loss or false success before persistence acknowledges.
                self.assertEqual(view.composer.text, "hello")
                self.assertEqual(view.transcript.control.messages, ())
                calls[0][2].set_result(SubmissionResult(message=message))
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, "")
                self.assertEqual([m.id for m in view.transcript.control.messages], ["m"])
                view.apply_snapshot(base)  # A stale projection cannot erase the receipt.
                self.assertEqual([m.id for m in view.transcript.control.messages], ["m"])
                committed = replace(message, status="committed")
                view.apply_snapshot(replace(base, messages=(committed, ChatMessage("assistant", "reply", id="a"))))
                self.assertEqual([m.id for m in view.transcript.control.messages], ["m", "a"])
                self.assertEqual(view.transcript.control.messages[0].status, "committed")
                self.assertFalse(view._submitted_messages)

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
                self.assertEqual(view.transcript.control.messages, ())
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
                self.assertEqual(view.transcript.control.messages, ())
            finally:
                view.output_renderers.close()
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
                future.set_exception(ValueError("admission rejected"))
                await asyncio.sleep(0)
                self.assertEqual(view.composer.text, "preserve me")
                self.assertEqual(view.transcript.control.messages, ())
                self.assertFalse(controller._submitting)
                self.assertFalse(controller._choosing_submission)
                self.assertIn("admission rejected", view.notice)
            finally:
                view.output_renderers.close()
                view.progress.close()
