"""Settings select project metadata immediately and load chat only on exit."""

import asyncio
import tempfile
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig
from hub.ui.application import create_application
from tests.hub.support import SizedOutput, eventually


class ProjectSettingsFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_selection_filter_and_save_close_all_drafts(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, auto_title=False),
                                                  input=pipe, output=SizedOutput())
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view.connected)
                old = view.project_id
                pipe.send_text("\x13")
                await eventually(lambda: screen.page is not None and not screen.busy)
                operations = []
                request = screen.request
                def record(operation, args, done):
                    operations.append((operation, args))
                    request(operation, args, done)
                screen.request = record
                with set_app(app):
                    screen.filter("engines.loop")
                    screen.switch_panel()
                    screen.project_action("c")
                await eventually(lambda: view._dialog is not None)
                pipe.send_text("Second project\r")
                await eventually(lambda: screen.drafts.current_project_id != old and not screen.busy)
                second = screen.drafts.current_project_id
                self.assertEqual(view.project_id, old)
                self.assertEqual(screen.page.query, "")
                self.assertIn("Second project", "".join(text for _, text in screen.action_title()))
                self.assertNotIn("activate_project", [op for op, _ in operations])
                with set_app(app):
                    screen.choose("profile")
                    field = screen.page.form.fields[("email",)]
                    field.input.text = "discard@example.com"
                    screen.page.cancel.handler()
                await eventually(lambda: not view.settings_open and view.project_id == second)
                self.assertEqual([op for op, _ in operations].count("activate_project"), 1)
                pipe.send_text("\x13")
                await eventually(lambda: view.settings_open and not screen.busy and screen.page is not None)
                with set_app(app):
                    screen.choose("profile")
                self.assertNotEqual(screen.page.form.fields[("email",)].input.text, "discard@example.com")
                screen.page.form.fields[("email",)].input.text = "save@example.com"
                with set_app(app):
                    screen.choose(old)
                await eventually(lambda: getattr(screen.page, "identifier", None) == old and not screen.busy)
                self.assertEqual(view.project_id, second)
                with set_app(app):
                    screen.page.save.handler()
                await eventually(lambda: not view.settings_open and view.project_id == old)
                pipe.send_text("\x13")
                await eventually(lambda: view.settings_open and not screen.busy)
                with set_app(app):
                    screen.choose("profile")
                self.assertEqual(screen.page.form.fields[("email",)].input.text, "save@example.com")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
