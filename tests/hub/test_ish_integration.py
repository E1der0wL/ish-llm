from tests.hub.support import minimize
"""Use the reference host's real set_float, set_key and prompt_async contracts."""

import asyncio
from pathlib import Path
import sys
import unittest

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.application.current import set_app
from prompt_toolkit.widgets import Label

from examples.hub.preview import install_preview
from tests.hub.support import settings_button, SizedOutput, eventually

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
    async def test_alt_navigation_preserves_input_focus_cursor_and_host_mouse_mode(self):
        from hub.widget.transcript.conversation import ChatMessage
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            mouse_support = prompt.app.mouse_support
            renderer_mouse_support = prompt.app.renderer.mouse_support
            installation = install_preview(prompt)
            self.assertEqual(prompt.app.mouse_support(), mouse_support())
            app, view = prompt.app, installation.view
            view.output.transcript.set_messages((ChatMessage("assistant", "\n\n".join(f"Line {i}" for i in range(150))),))
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running and view.output.transcript.window.render_info is not None)
                self.assertTrue(app.mouse_support())
                pipe.send_text("keep this draft")
                await eventually(lambda: view.composer.text == "keep this draft")
                cursor = view.composer.buffer.cursor_position
                control = view.output.transcript.control
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
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertEqual(control.top_line, 0)
                self.assertEqual(view.composer.text, "keep this draft")
                self.assertEqual(view.composer.buffer.cursor_position, cursor)
            finally:
                app.exit(result="")
                await task
                installation.close()
                self.assertIs(app.mouse_support, mouse_support)
                self.assertIs(app.renderer.mouse_support, renderer_mouse_support)

    async def test_mouse_support_preserves_dynamic_host_filter_and_later_plugin_change(self):
        from prompt_toolkit.filters import Condition
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            enabled = False
            original = Condition(lambda: enabled)
            prompt.app.mouse_support = prompt.app.renderer.mouse_support = original
            installation = install_preview(prompt)
            try:
                self.assertFalse(prompt.app.mouse_support())
                self.assertFalse(prompt.app.renderer.mouse_support())
                enabled = True
                self.assertTrue(prompt.app.mouse_support())
                self.assertTrue(prompt.app.renderer.mouse_support())
                installation.close()
                self.assertIs(prompt.app.mouse_support, original)
                self.assertIs(prompt.app.renderer.mouse_support, original)
                installation = install_preview(prompt)
                external = Condition(lambda: False)
                prompt.app.mouse_support = prompt.app.renderer.mouse_support = external
                installation.close()
                self.assertIs(prompt.app.mouse_support, external)
                self.assertIs(prompt.app.renderer.mouse_support, external)
            finally:
                installation.close()

    async def test_escape_and_alt_scroll_cross_modal_binding_scope(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install_preview(prompt)
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
                await eventually(lambda: view.output.transcript.control.top_line == 1)
                self.assertEqual(view.selected, selected)
                self.assertIs(app.layout.current_control, view._session_control)
                pipe.send_text("\x1b[Ccheck focus")
                await eventually(lambda: view.composer.text == "check focus")
                pipe.send_text("\x1b\x1b[C")
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
            installation = install_preview(prompt)
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
                with set_app(app):
                    view.output.tags.open()
                for columns, rows in ((132, 40), (80, 24), (160, 60), (60, 14), (132, 40)):
                    with self.subTest(columns=columns, rows=rows):
                        output.columns, output.rows = columns, rows
                        rendered.clear()
                        # Exercise the redraw path PTK uses after SIGWINCH.
                        app._on_resize()
                        await asyncio.wait_for(rendered.wait(), 3)
                        positions = app.renderer._last_screen.visible_windows_to_write_positions
                        hub_positions = [position for window, position in positions.items()
                                         if "class:hub" in (window.style() if callable(window.style) else window.style)]
                        self.assertEqual(max(p.xpos + p.width for p in hub_positions), columns)
                        self.assertEqual(max(p.ypos + p.height for p in hub_positions), rows)
                        self.assertIn(view.composer.window, positions)
                        self.assertIn(view.output.transcript.window, positions)
                        self.assertEqual(view._sidebar.filter(), columns >= 88)
                        self.assertTrue(view.output.tags.container.filter())
                        self.assertIs(app.layout.current_control, view.output.tags.control)
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
            installation = install_preview(prompt)
            replacement = install_preview(prompt)
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
                    pipe.send_text("\x1b[C" if control == view.composer.control else "\x1b")
                    await eventually(lambda: prompt.app.layout.current_control == control)
                for control in (view.composer.control, view._session_control):
                    pipe.send_text("\x1b[C" if control == view.composer.control else "\x1b")
                    await eventually(lambda: prompt.app.layout.current_control == control)
                await minimize(pipe, replacement.view)
                await eventually(lambda: not replacement.view.visible)
                self.assertIs(prompt.app.current_buffer, prompt.default_buffer)
                self.assertEqual(prompt.default_buffer.text, "shell draft")
                pipe.send_text('\x11\x10\r')
                await eventually(lambda: replacement.view.settings_open)
                view.settings.choose("appearance")
                self.assertIn(prompt.app.layout.current_control, [view.settings.global_list, view.settings.project_list])
                pipe.send_text("\x1b[C")  # Right enters the settings fields.
                await eventually(lambda: prompt.app.layout.current_control == view.settings.page.body_widgets()[0].control)
                settings_button(prompt.app, pipe, view, "cancel")
                from tests.hub.support import open_home
                await open_home(prompt.app, view)
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
