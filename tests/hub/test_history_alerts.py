from tests.hub.test_mockup import minimize
"""Exercise dialogs and shell alerts through real host rendering and backend APIs."""

import asyncio
from functools import partial
import tempfile
import threading
import unittest

from prompt_toolkit.input import create_pipe_input
from hub.hub import install
from hub.ui.application import create_application
from hub.widget.conversation import ChatMessage
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.worker import BackendWorker
from tests.hub.test_ish_integration import Prompt
from tests.hub.test_live import controlled_backend, until
from tests.hub.test_mockup import SizedOutput, eventually
from tests.hub.test_overlays import screen_text


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_session_completion_notifies_while_in_shell(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            gate = threading.Event()
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, config=HubConfig(directory, "loop", "test/model", auto_title=False))
            installation.controller.worker_factory = partial(BackendWorker,
                backend_factory=partial(controlled_backend, gate=gate))
            view, app = installation.view, prompt.app
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await until(lambda: app.is_running)
                pipe.send_text("shell draft\x11")
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("\x1bOS\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("hold finish\r")
                await until(lambda: any(m.status == "streaming" for m in view.transcript.control.messages))
                await minimize(pipe, view)
                await until(lambda: not view.visible)
                gate.set()
                await until(lambda: any(item.level == "success" for item in view.toasts.items))
                await until(lambda: "" in screen_text(app, 132, 40))
                self.assertIs(view.toasts.app, app)
                self.assertEqual(prompt.default_buffer.text, "shell draft")
                self.assertIs(app.current_buffer, prompt.default_buffer)
            finally:
                app.exit(result="")
                await task
                await asyncio.to_thread(installation.close)

    async def test_ctrl_g_delete_and_ctrl_l_activity(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            factory = partial(BackendWorker, backend_factory=partial(controlled_backend, gate=threading.Event()))
            app, controller = create_application(HubConfig(directory, "loop", "test/model", auto_title=False),
                                                input=pipe, output=SizedOutput(), worker_factory=factory)
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("\x1bOS\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("question\r")
                await until(lambda: any(m.status == "completed" for m in view.transcript.control.messages))
                pipe.send_text("\x07")
                await until(lambda: "1. question" in screen_text(app, 132, 40))
                pipe.send_text("d")
                await until(lambda: "원본 저장 이벤트" in screen_text(app, 132, 40))
                pipe.send_text("\r")
                await until(lambda: view.t("history_empty") in screen_text(app, 132, 40))
                self.assertFalse(any(m.text == "question" for m in view.transcript.control.messages))
                pipe.send_text("\x1b")
                await until(lambda: view._dialog is None)
                pipe.send_text("\x0c")
                await until(lambda: "run.completed" in screen_text(app, 132, 40))
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_output_scrollbar_tracks_top_and_bottom(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            view.transcript.control.messages = (ChatMessage("assistant", "\n\n".join(str(i) for i in range(180))),)
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running and view.transcript.window.render_info is not None)
                def thumb_at(bottom):
                    position = app.renderer._last_screen.visible_windows_to_write_positions[view.transcript.window]
                    y = position.ypos + position.height - 2 if bottom else position.ypos + 1
                    cell = app.renderer._last_screen.data_buffer[y][position.xpos + position.width - 1]
                    return cell.char == " " and "class:hub.scrollbar-thumb" in cell.style
                view.transcript.control.scroll("home")
                app.invalidate()
                await eventually(lambda: thumb_at(False))
                view.transcript.control.scroll("end")
                app.invalidate()
                await eventually(lambda: thumb_at(True))
                self.assertFalse(thumb_at(False))
                self.assertGreater(view.transcript.window.vertical_scroll, 0)
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_turn_catalog_branch_delete_and_project_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, "loop", "test/model", auto_title=False),
                                 partial(controlled_backend, gate=threading.Event()))
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                source = runtime.selected_id
                for text in ("first", "second"):
                    await runtime.submit(source, text)
                    await runtime.sessions[source].run.wait_idle()
                rows = await runtime.turns(source)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["engine"], "loop")
                self.assertIsNotNone(rows[0]["elapsed"])
                branch = await runtime.new_session("clone", "branch", source, rows[0]["id"])
                self.assertEqual(len(await runtime.sessions[branch].aconversation()), 2)
                await runtime.delete_turn(source, rows[0]["id"])
                self.assertEqual([r["text"] for r in await runtime.turns(source)], ["second"])
                activity = await runtime.project_activity()
                self.assertEqual(sum(r["event"] == "run.completed" for r in activity), 2)
                self.assertTrue(all(r["source"]["session_id"] == source for r in activity))
            finally:
                await runtime.close()

    async def test_shell_alert_preserves_draft_and_uses_severity_colors(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("shell draft")
                await eventually(lambda: prompt.default_buffer.text == "shell draft")
                view.toasts.push("Session alpha", "failed", ("p", "s"))
                await eventually(lambda: "Session alpha" in screen_text(app, 132, 40))
                self.assertIn("", screen_text(app, 132, 40))
                self.assertIs(app.current_buffer, prompt.default_buffer)
                self.assertEqual(prompt.default_buffer.text, "shell draft")
                screen = app.renderer._last_screen
                backgrounds = {app._merged_style.get_attrs_for_style_str(cell.style).bgcolor
                               for line in screen.data_buffer.values() for cell in line.values()}
                self.assertIn(view.theme.alert_error_background[1:], backgrounds)
            finally:
                app.exit(result="")
                await task
                installation.close()
