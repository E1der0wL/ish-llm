from tests.hub.test_mockup import minimize
"""Use the reference host's real set_float, set_key and prompt_async contracts."""

import asyncio
from pathlib import Path
import sys
import unittest

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.widgets import Label

from hub.hub import install
from tests.hub.test_mockup import SizedOutput, eventually

HOST = Path(__file__).resolve().parents[2] / "ish.platform/src"


def Prompt(*args, **kwargs):
    """참조 Host가 없는 배포 checkout에서는 실제 Host를 요구하는 검사만 skip한다."""
    if not (HOST / "ish/ui/prompt.py").is_file():
        raise unittest.SkipTest("Reference ish.platform host is not part of the plugin checkout")
    if str(HOST) not in sys.path:
        sys.path.insert(0, str(HOST))
    from ish.ui.prompt import Prompt as HostPrompt
    return HostPrompt(*args, **kwargs)


class IshIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_alt_navigation_and_mouse_scroll_preserve_input_focus_and_cursor(self):
        from hub.widget.conversation import ChatMessage
        from prompt_toolkit.data_structures import Point
        from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            view.transcript.set_messages((ChatMessage("assistant", "\n\n".join(f"Line {i}" for i in range(150))),))
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running and view.transcript.window.render_info is not None)
                pipe.send_text("keep this draft")
                await eventually(lambda: view.composer.text == "keep this draft")
                cursor = view.composer.buffer.cursor_position
                control = view.transcript.control
                page = max(1, control._height - 2)
                for sequence, line in (("\x1b[6~", page), ("\x1b[B", page + 1),
                                       ("\x1b[A", page), ("\x1b[5~", 0)):
                    pipe.send_text("\x1b" + sequence)
                    await eventually(lambda: control.top_line == line)
                pipe.send_text("\x1b\x1b[F")
                await eventually(lambda: control.top_line == len(control._lines) - control._height)
                pipe.send_text("\x1b\x1b[H")
                await eventually(lambda: control.top_line == 0)
                pipe.send_text("\x1b\x1b[C\x1b\x1b[D")
                await asyncio.sleep(0.05)
                for event_type in (MouseEventType.MOUSE_UP, MouseEventType.SCROLL_DOWN):
                    control.mouse_handler(MouseEvent(Point(0, 0), event_type, MouseButton.LEFT, frozenset()))
                    self.assertIs(app.layout.current_control, view.composer.control)
                self.assertEqual(control.top_line, 3)
                self.assertEqual(view.composer.text, "keep this draft")
                self.assertEqual(view.composer.buffer.cursor_position, cursor)
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_escape_and_alt_scroll_cross_modal_binding_scope(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            # A widget can stop ancestor binding lookup at its own modal boundary.
            # Host-level navigation must still work when that widget has focus.
            view._sidebar.content.modal = True
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                pipe.send_text("\x1b")
                await eventually(lambda: app.layout.current_control == view._session_control)
                selected = view.selected
                pipe.send_text("\x1b\x1b[B")
                await eventually(lambda: view.transcript.control.top_line == 1)
                self.assertEqual(view.selected, selected)
                self.assertIs(app.layout.current_control, view._session_control)
                pipe.send_text("\tcheck focus")
                await eventually(lambda: view.composer.text == "check focus")
                pipe.send_text("\x1b\t")
                await eventually(lambda: app.layout.current_control == view.composer.control)
                self.assertEqual(view.composer.text, "check focus")
            finally:
                if not task.done():
                    app.exit(result="")
                await task
                installation.close()

    async def test_open_hub_tracks_terminal_resize_and_preserves_draft(self):
        with create_pipe_input() as pipe:
            output = SizedOutput()
            prompt = Prompt(input=pipe, output=output)
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            rendered = asyncio.Event()
            app.after_render += lambda _: rendered.set()
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("shell draft\x11")
                await eventually(lambda: view.visible)
                pipe.send_text("keep this draft")
                await eventually(lambda: view.composer.text == "keep this draft")
                view.show_details = True
                for columns, rows in ((132, 40), (80, 24), (160, 60), (60, 14), (132, 40)):
                    with self.subTest(columns=columns, rows=rows):
                        output.columns, output.rows = columns, rows
                        rendered.clear()
                        # Exercise the redraw path PTK uses after SIGWINCH.
                        app._on_resize()
                        await asyncio.wait_for(rendered.wait(), 3)
                        positions = app.renderer._last_screen.visible_windows_to_write_positions
                        hub_positions = [position for window, position in positions.items()
                                         if "class:hub" in window.style]
                        self.assertEqual(max(p.xpos + p.width for p in hub_positions), columns)
                        self.assertEqual(max(p.ypos + p.height for p in hub_positions), rows)
                        self.assertIn(view.composer.window, positions)
                        self.assertIn(view.transcript.window, positions)
                        self.assertEqual(view._sidebar.filter(), columns >= 88)
                        self.assertEqual(view._details.filter(), columns >= 116)
                        self.assertIs(app.layout.current_control, view.composer.control)
                        self.assertEqual(view.composer.text, "keep this draft")
                await minimize(pipe, view)
                await eventually(lambda: not view.visible)
                self.assertEqual(prompt.default_buffer.text, "shell draft")
                self.assertIs(app.current_buffer, prompt.default_buffer)
            finally:
                if not task.done():
                    app.exit(result="")
                await task
                installation.close()

    async def test_actual_host_focus_keys_and_reinstallation(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            original_style = prompt.style
            original_bindings = prompt.app.key_bindings
            previous_q = lambda event: None
            previous_s = lambda event: None
            prompt.set_key("c-q", handler=previous_q)
            prompt.set_key("c-s", handler=previous_s)
            other_float = prompt.set_float(Label("Other plugin"), right=0)
            installation = install(prompt, preview=True)
            replacement = install(prompt, preview=True)
            replacement.view.show_preview = True
            self.assertTrue(installation._closed)
            self.assertEqual(len(prompt.floats), 3)
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: prompt.app.is_running)
                pipe.send_text("shell draft\x11")
                await eventually(lambda: replacement.view.visible)
                pipe.send_text("ui draft\r")
                await eventually(lambda: "전송 미리보기" in replacement.view.notice)
                self.assertFalse(task.done(), "Hub Enter must not dispatch a shell command")
                self.assertEqual(prompt.default_buffer.text, "shell draft")
                self.assertEqual(replacement.view.composer.text, "ui draft")
                pipe.send_text("\x00second line")
                await eventually(lambda: replacement.view.composer.text == "ui draft\nsecond line")
                replacement.view.composer.text = ""
                pipe.send_text("/eng")
                await eventually(lambda: replacement.view.composer.buffer.complete_state is not None)
                pipe.send_text("\x1b[B\t")
                await eventually(lambda: replacement.view.composer.buffer.complete_state is None)
                self.assertEqual(replacement.view.composer.text, "/engine")
                self.assertIsNone(replacement.view._dialog)
                replacement.view.composer.text = "ui draft"
                pipe.send_text("\x1b")  # ESC
                await eventually(lambda: prompt.app.layout.current_control == replacement.view._session_control)
                view = replacement.view
                for control in (view.composer.control, view._session_control):
                    pipe.send_text("\t" if control == view.composer.control else "\x1b")
                    await eventually(lambda: prompt.app.layout.current_control == control)
                for control in (view.composer.control, view._session_control):
                    pipe.send_text("\t" if control == view.composer.control else "\x1b")
                    await eventually(lambda: prompt.app.layout.current_control == control)
                await minimize(pipe, replacement.view)
                await eventually(lambda: not replacement.view.visible)
                self.assertIs(prompt.app.current_buffer, prompt.default_buffer)
                self.assertEqual(prompt.default_buffer.text, "shell draft")
                pipe.send_text("\x11\x13")
                await eventually(lambda: replacement.view.settings_open)
                view.settings.choose("appearance")
                self.assertIn(prompt.app.layout.current_control, [view.settings.global_list, view.settings.project_list])
                pipe.send_text("\t")  # Tab enters the settings fields.
                await eventually(lambda: prompt.app.layout.current_control == view.settings.page.body_widgets()[0].control)
                pipe.send_text("\t\x1b[Z\x03")  # Preview has no settings backend; close without saving.
                await eventually(lambda: not view.settings_open)
                self.assertEqual(view.composer.text, "ui draft")
                await minimize(pipe, view)
                await eventually(lambda: not view.visible)
                self.assertEqual(prompt.default_buffer.text, "shell draft")
                replacement.close()
                self.assertEqual(prompt.floats, [other_float])
                self.assertIs(prompt.app.key_bindings, original_bindings)
                self.assertIs(prompt.style, original_style)
                self.assertEqual(prompt.key_bindings.get_bindings_for_keys(("c-q",))[0].handler, previous_q)
                self.assertEqual(prompt.key_bindings.get_bindings_for_keys(("c-s",))[0].handler, previous_s)
            finally:
                if not task.done():
                    prompt.app.exit(result="")
                await task
                replacement.close()


if __name__ == "__main__":
    unittest.main()
