"""Progress survives layout changes without taking focus or printing to stdout."""

import asyncio
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.formatted_text.utils import fragment_list_width

from examples.hub.preview import create_preview
from hub.ui.chat.component_ui import ComponentCommands
from hub.widget.progress import ProgressControl
from tests.hub.test_mockup import SizedOutput, eventually


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.input_context = create_pipe_input()
        self.pipe = self.input_context.__enter__()
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput())
        self.task = asyncio.create_task(self.app.run_async())
        await eventually(lambda: self.app.is_running)
        self.pipe.send_text("\x11")
        await eventually(lambda: self.view.visible)

    async def asyncTearDown(self):
        self.view.progress.close()
        self.app.exit()
        await self.task
        self.input_context.__exit__(None, None, None)

    async def test_render_resize_percentage_and_focus(self):
        with set_app(self.app), redirect_stdout(StringIO()) as terminal:
            progress = self.view.progress
            region = progress.container()
            control = region.content
            self.assertEqual(region.preferred_height(100, 40).preferred, 1)
            self.assertEqual(control.create_content(100, 1).get_line(0), [])
            first = progress.start("문서 [red]이름[/red]")
            content = control.create_content(100, 3)
            text = "".join(fragment[1] for fragment in content.get_line(0))
            self.assertIn("[red]", text)
            self.assertNotIn("%", text)
            self.assertFalse(control.is_focusable())
            self.assertIsNotNone(progress._timer)
            self.assertLess(fragment_list_width(control.create_content(240, 1).get_line(0)), 120)
            second = progress.start("설정", total=10)
            progress.update(second, completed=4)
            content = control.create_content(100, 3)
            text = "".join(f[1] for f in content.get_line(0))
            self.assertIn("40%", text)
            self.assertIn("외 1개", text)
            self.assertEqual(control.create_content(20, 3).line_count, 1)
            self.assertEqual(region.preferred_height(100, 40).preferred, 1)
            self.assertIs(self.app.layout.current_control, self.view.composer.control)
            progress.finish(second)
            self.assertTrue(progress.active)
            self.assertIn("문서", "".join(f[1] for f in control.create_content(100, 1).get_line(0)))
            progress.finish(first)
            self.assertFalse(progress.active)
            self.assertIsNone(progress._timer)
            self.assertEqual(region.preferred_height(100, 40).preferred, 1)
            self.assertEqual(control.create_content(100, 1).get_line(0), [])
        self.assertEqual(terminal.getvalue(), "")

    async def test_settings_pending_selection_errors_and_shared_background_task(self):
        callbacks = []
        view = self.view
        view.settings.request = lambda operation, args, done: callbacks.append(done)
        with set_app(self.app):
            other = view.progress.start("RAG")
        self.pipe.send_text("\x13")
        await eventually(lambda: view.settings.busy)
        self.assertEqual(view.settings.status, "")
        self.assertEqual(len(view.progress.progress.tasks), 2)
        self.assertIs(self.app.layout.current_control, view.settings.left_control())
        with set_app(self.app):
            callbacks.pop()(None, RuntimeError("load failed"))
            self.assertIn("load failed", view.settings.status)
            self.assertEqual(len(view.progress.progress.tasks), 1)
            def broken(*args):
                raise RuntimeError("dispatch failed")
            view.settings.request = broken
            view.settings.call("catalog")
            self.assertFalse(view.settings.busy)
            self.assertIn("dispatch failed", view.settings.status)
            view.progress.finish(other)
        self.assertFalse(view.progress.active)

    async def test_rag_registration_keeps_input_and_reports_completion_without_popup(self):
        view = self.view
        view.project_id = "project"
        view.component_commands = {"rag": "rag"}
        completions = []
        controller = SimpleNamespace(view=view, app=self.app,
            _call=lambda *args, completed: completions.append(completed), _error=self.fail)
        commands = ComponentCommands(controller)
        with set_app(self.app):
            view.composer.text = '/rag create note {"title":"Memo","content":"text"}'
            commands.open("rag", 'create note {"title":"Memo","content":"text"}')
            self.assertTrue(view.progress.active)
            self.assertIsNone(view._dialog)
            self.assertIs(self.app.layout.current_control, view.composer.control)
            view.composer.text = "Next request"
            completions.pop()({"id": "note"})
            self.assertEqual(view.composer.text, "Next request")
            self.assertFalse(view.progress.active)
            self.assertIsNone(view._dialog)
            self.assertIn("note", view.toasts.text())
            commands.open("rag", 'update note {"content":"text"}')
            completions.pop()(None)
            self.assertFalse(view.progress.active)
            self.assertFalse(commands.busy)
