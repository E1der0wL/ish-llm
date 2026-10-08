"""Immediate feedback, periodic observations, incremental layout and queue cancellation."""

import asyncio
from dataclasses import replace
from functools import partial
from types import SimpleNamespace as NS
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from examples.hub.preview import create_preview

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.worker import BackendWorker
from hub.config.theme import HubTheme
from hub.model import ChatMessage, HubSnapshot, SessionSummary
from hub.ui.live import LiveHubView
from hub.ui.chat.history_ui import HistoryUI
from hub.widget.conversation import ConversationControl, render_messages
from tests.hub.test_live import controlled_backend
from tests.hub.test_mockup import SizedOutput, eventually


class ResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_c_key_confirms_and_refreshes_cancelled_row(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.project_id = "p"
            view._submitted_messages = {}
            sid = view.sessions[0].id
            rows = [{"id": "q", "text": "queued", "status": "queued", "time": "", "engine": "",
                     "elapsed": None, "response": "", "error": ""}]
            calls = []
            def call(operation, *args, completed):
                calls.append((operation, args))
                if operation == "cancel_request":
                    rows[0]["status"] = "cancelled"
                    completed(True)
                elif operation == "turns":
                    completed(rows)
                else:
                    completed(None)
            history = HistoryUI(NS(view=view, _call=call, _snapshot=lambda _: None))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    history.show(sid, rows)
                pipe.send_text("c")
                await eventually(lambda: view._dialog.title == view.t("history_cancel"))
                self.assertFalse(calls)
                pipe.send_text("\r")
                await eventually(lambda: ("cancel_request", (sid, "q")) in calls)
                await eventually(lambda: view._dialog.title == view.t("history_title"))
                self.assertEqual(rows[0]["status"], "cancelled")
            finally:
                app.exit()
                await task
                view.output_renderers.close()
                view.progress.close()

    async def test_periodic_reads_without_events_use_configured_cadence(self):
        reads = []
        async def snapshot():
            reads.append(asyncio.get_running_loop().time())
            await asyncio.sleep(.04)
            return len(reads)
        worker = BackendWorker.__new__(BackendWorker)
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), snapshot=snapshot)
        worker._output_interval = .1
        worker._interval_changed = asyncio.Event()
        worker.publish = Mock()
        worker.report_error = Mock()
        task = asyncio.create_task(worker._refresh())
        try:
            async with asyncio.timeout(.8):
                while len(reads) < 4:
                    await asyncio.sleep(.005)
            self.assertLess(reads[3] - reads[0], .5)
            worker.report_error.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def test_preparation_survives_stale_snapshot_and_stops_on_own_response(self):
        view = LiveHubView()
        try:
            base = HubSnapshot("p", "P", "model", "file", (SessionSummary("s", "S", "idle"),), "s", ())
            view.apply_snapshot(base)
            view.preparing.start(("p", "s"))
            self.assertTrue(view.preparing.visible())
            self.assertIn(view.t("preparing_response"), view.preparing.text())
            self.assertEqual(view.transcript.control.messages, ())
            view.preparing.accepted(("p", "s"), "request")
            view.apply_snapshot(base)
            self.assertTrue(view.preparing.visible())
            user = ChatMessage("user", "hello", id="request", status="committed")
            view.apply_snapshot(replace(base, messages=(user,)))
            self.assertTrue(view.preparing.visible())
            view.apply_snapshot(replace(base, messages=(user, ChatMessage("assistant", "", id="answer", status="streaming"))))
            self.assertFalse(view.preparing.visible())
        finally:
            view.output_renderers.close()
            view.progress.close()

    def test_unchanged_messages_reuse_layout_and_objects_keep_offsets(self):
        theme = HubTheme()
        messages = (ChatMessage("assistant", "```python\nx = 1\n```", id="a"),
                    ChatMessage("user", "next"), ChatMessage("assistant", "start", id="b", status="streaming"))
        control = ConversationControl(messages, theme)
        try:
            with patch("hub.widget.conversation.render_messages", wraps=render_messages) as render:
                control.create_content(80, 20)
                self.assertEqual(render.call_count, 3)
                control.messages = (*messages[:2], replace(messages[2], text="start and more"))
                control.create_content(80, 20)
                self.assertEqual(render.call_count, 4)
                anchors, objects = [], []
                expected = render_messages(control.messages, 80, theme, control.language, anchors=anchors,
                    objects=objects, renderers=control.renderers, root=control.file_root)
                self.assertEqual(control._lines, expected)
                self.assertEqual(control._anchors, anchors)
                self.assertEqual(control.objects, objects)
                control.create_content(60, 20)
                self.assertEqual(render.call_count, 7)
        finally:
            control.renderers.close()

    async def test_cancel_queued_request_preserves_active_run_and_reopen(self):
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, model="test/model", auto_title=False)
            factory = partial(controlled_backend, gate=gate)
            runtime = HubRuntime(config, factory)
            try:
                await runtime.start()
                sid = await runtime.new_session(title="Queue")
                first = await runtime.submit(sid, "hold active")
                session = runtime.sessions[sid]
                async with asyncio.timeout(3):
                    while not (await session.run.astatus()).active_run_id:
                        await asyncio.sleep(.01)
                active = (await session.run.astatus()).active_run_id
                queued = await runtime.submit(sid, "cancel this")
                self.assertTrue(await runtime.cancel_request(sid, queued))
                self.assertEqual((await session.run.astatus()).active_run_id, active)
                self.assertEqual((await session.run.astatus()).queued_count, 0)
                self.assertEqual(next(r for r in await runtime.turns(sid) if r["id"] == queued)["status"], "cancelled")
                with self.assertRaises(ValueError) as error:
                    await runtime.cancel_request(sid, first)
                self.assertEqual(str(error.exception), runtime.t("history_cancel_unavailable"))
                gate.set()
                await session.run.wait_idle()
            finally:
                gate.set()
                await runtime.close()
            runtime = HubRuntime(config, factory)
            try:
                await runtime.start()
                self.assertEqual((await runtime.sessions[sid].run.astatus()).queued_count, 0)
                self.assertEqual(len(await runtime.sessions[sid].run.alist()), 1)
                self.assertTrue(await runtime.delete_turn(sid, queued))
                self.assertNotIn(queued, [row["id"] for row in await runtime.turns(sid)])
            finally:
                await runtime.close()
