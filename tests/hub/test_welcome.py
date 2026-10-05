"""No hidden session creation on startup, deletion, reopening or project switching."""

import asyncio
import tempfile
import unittest
from prompt_toolkit.input import create_pipe_input
from hub.backend.runtime import HubConfig, HubRuntime
from hub.ui.application import create_application
from hub.widget.welcome import logo
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput


class WelcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_project_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, auto_title=False)
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                self.assertEqual(await runtime.project.sessions.alist(), [])
                self.assertEqual((await runtime.snapshot()).sessions, ())
                self.assertEqual(runtime.selected_id, "")
                with self.assertRaises(ValueError):
                    await runtime.submit("", "No implicit session")
                first = await runtime.new_session(title="First")
                self.assertEqual(await runtime.delete_session(first), "")
                self.assertEqual(await runtime.project.sessions.alist(), [])
            finally:
                await runtime.close()
            reopened = HubRuntime(config)
            try:
                await reopened.start()
                self.assertFalse(reopened.sessions)
                other = await reopened.backend.projects.acreate("Other", components=[], conversation_storage="file")
                await reopened.activate_project(other)
                self.assertFalse(reopened.sessions)
            finally:
                await reopened.close()

    async def test_welcome_create_and_delete_last_session(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, auto_title=False), input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected and view.no_sessions)
                await until(lambda: view.welcome.render_info is not None)
                self.assertGreater(len(logo(80)), 1)
                self.assertEqual(logo(10), ("HUB",))
                self.assertFalse(view.welcome.content.is_focusable())
                pipe.send_text("\x1b")
                await until(lambda: app.layout.current_control == view._session_control)
                pipe.send_text("c")
                await until(lambda: view._dialog is not None)
                pipe.send_text("My session\r")
                await until(lambda: not view.no_sessions and view._dialog is None)
                self.assertEqual(view.sessions[view.selected].title, "My session")
                controller.delete_session(view.sessions[view.selected].id)
                await until(lambda: view.no_sessions)
                self.assertEqual((await asyncio.wrap_future(controller.worker.call("snapshot"))).sessions, ())
                pipe.send_text("\x13")
                await until(lambda: view.settings_open and not view.settings.busy)
                pipe.send_text("\x13")
                await until(lambda: not view.settings_open)
                self.assertIs(app.layout.current_control, view.composer.control)
                pipe.send_text("Keep my draft\r")
                await until(lambda: view._dialog is not None)
                pipe.send_text("Second\r")
                await until(lambda: not view.no_sessions and view._dialog is None)
                self.assertEqual(view.composer.text, "Keep my draft")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
