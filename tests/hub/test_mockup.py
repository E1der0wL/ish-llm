"""Exercise real prompt-toolkit rendering and input dispatch without a terminal."""

import asyncio
import unittest

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from examples.hub.preview import create_preview
from hub.config.theme import HubTheme


class SizedOutput(DummyOutput):
    def __init__(self, columns=132, rows=40):
        self.columns = columns
        self.rows = rows

    def get_size(self):
        return Size(rows=self.rows, columns=self.columns)


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


async def minimize(pipe, view):
    if view.tags.visible:
        pipe.send_text("\x14")
        await eventually(lambda: not view.tags.visible)
    if view._dialog is not None:
        pipe.send_text("\x1b")
        await eventually(lambda: view._dialog is None)
    if not view.active_page.sidebar_focused:
        pipe.send_text("\x1b")
        await eventually(lambda: view.active_page.sidebar_focused)
    pipe.send_text("\x1b")
    await eventually(lambda: not view.visible)


class MockupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.input_context = create_pipe_input()
        self.pipe = self.input_context.__enter__()
        self.output = SizedOutput()
        self.app, self.view, self.shell = create_preview(input=self.pipe, output=self.output)
        self.task = asyncio.create_task(self.app.run_async())
        await eventually(lambda: self.app.is_running)

    async def asyncTearDown(self):
        if not self.task.done():
            self.app.exit()
        await self.task
        self.input_context.__exit__(None, None, None)

    async def open_hub(self):
        self.pipe.send_text("\x11")
        await eventually(lambda: self.view.visible)

    async def test_toggle_preserves_shell_and_composer(self):
        self.pipe.send_text("unfinished shell")
        await eventually(lambda: self.shell.text == "unfinished shell")
        await self.open_hub()
        self.pipe.send_text("draft")
        await eventually(lambda: self.view.composer.text == "draft")
        await minimize(self.pipe, self.view)
        await eventually(lambda: not self.view.visible)
        self.assertIs(self.app.layout.current_control, self.shell.control)
        self.assertEqual(self.shell.text, "unfinished shell")
        await self.open_hub()
        self.assertEqual(self.view.composer.text, "draft")
        self.assertIs(self.app.layout.current_control, self.view.composer.control)

    async def test_submit_is_disconnected_and_multiline_works(self):
        await self.open_hub()
        self.pipe.send_text("hello\r")
        await eventually(lambda: "전송 미리보기" in self.view.notice)
        self.assertEqual(self.view.composer.text, "hello")
        self.pipe.send_text("\x00world")
        await eventually(lambda: self.view.composer.text == "hello\nworld")
        self.assertFalse(self.task.done())

    async def test_session_drafts_and_modal_focus_cycle(self):
        await self.open_hub()
        self.pipe.send_text("first\x1b")
        await eventually(lambda: self.app.layout.current_control == self.view._session_control)
        self.pipe.send_text("\x1b[B\x1b[C")
        await eventually(lambda: self.view.selected == 1)
        self.assertEqual(self.view.composer.text, "")
        self.pipe.send_text("second\x1b")  # ESC
        await eventually(lambda: self.app.layout.current_control == self.view._session_control)
        self.pipe.send_text("\x1b[A")  # up
        await eventually(lambda: self.view.selected == 0)
        self.assertEqual(self.view.composer.text, "first")
        self.pipe.send_text("\t")  # Tab enters the main panel.
        await eventually(lambda: self.app.layout.current_control == self.view.composer.control)

    async def test_narrow_layout_hides_optional_panels(self):
        self.output.columns = 80
        self.output.rows = 24
        await self.open_hub()
        self.pipe.send_text("\x14")
        await eventually(lambda: self.view.tags.visible)
        self.assertTrue(self.view._details.filter())
        self.assertFalse(self.view._sidebar.filter())
        self.pipe.send_text("\x14")
        await eventually(lambda: not self.view.tags.visible)
        self.pipe.send_text("\x1b")
        await eventually(lambda: self.app.layout.current_control == self.view._session_control)
        self.assertTrue(self.view._sidebar.filter())
        self.pipe.send_text("\t")
        await eventually(lambda: self.app.layout.current_control == self.view.composer.control)
        self.assertFalse(self.view._sidebar.filter())

    async def test_markdown_scrolling_and_live_theme_preserve_input(self):
        await self.open_hub()
        self.view.show_preview = False
        self.pipe.send_text("keep draft")
        await eventually(lambda: self.view.composer.text == "keep draft"
                         and self.view.transcript.window.render_info is not None)
        self.pipe.send_text("\x1b\x1b[F")  # Alt+End
        await eventually(lambda: self.view.transcript.control.cursor_line > 0)
        self.view.set_theme(HubTheme.dark())
        self.app.invalidate()
        self.assertEqual(self.view.composer.text, "keep draft")
        self.assertEqual(self.view.transcript.control.theme, HubTheme.dark())
        self.pipe.send_text("\x1b\x1b[H")  # Alt+Home
        await eventually(lambda: self.view.transcript.control.cursor_line == 0)


if __name__ == "__main__":
    unittest.main()
