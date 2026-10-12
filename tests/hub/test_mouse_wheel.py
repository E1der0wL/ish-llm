"""Terminal mouse packets scroll chat without taking focus from the composer."""

import asyncio
import unittest

from prompt_toolkit.input import create_pipe_input
from examples.hub.preview import install_preview
from hub.model import ChatMessage
from tests.hub.test_ish_integration import Prompt
from tests.hub.support import SizedOutput, eventually, minimize


class MouseWheelTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_packets_over_output_and_composer_preserve_draft_and_shell_mode(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            app = prompt.app
            original = app.mouse_support
            installation = install_preview(prompt)
            view = installation.view
            # Windows painted after their parent must retain wheel routing too.
            view.composer.window.z_index = 10
            view.transcript.set_messages((ChatMessage("assistant", "\n\n".join(f"Line {i}" for i in range(200))),))
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view.transcript.window.render_info is not None)
                self.assertTrue(app.mouse_support())
                draft = "\n".join(f"draft {i}" for i in range(8))
                view.composer.text = draft
                app.invalidate()
                await eventually(lambda: view.composer.window.render_info.window_height > 1)
                cursor = view.composer.buffer.cursor_position
                control = view.transcript.control
                for window in (view.transcript.window, view.composer.window):
                    position = app.renderer._last_screen.visible_windows_to_write_positions[window]
                    x, y = position.xpos + 1, position.ypos + 1
                    before = control.top_line
                    pipe.send_text(f"\x1b[<65;{x};{y}M")
                    await eventually(lambda: control.top_line == before + 3)
                    self.assertIs(app.layout.current_control, view.composer.control)
                    self.assertEqual(view.composer.text, draft)
                    self.assertEqual(view.composer.buffer.cursor_position, cursor)
                    pipe.send_text(f"\x1b[<64;{x};{y}M")
                    await eventually(lambda: control.top_line == before)
                await minimize(pipe, view)
                self.assertEqual(app.mouse_support(), original())
            finally:
                app.exit(result="")
                await task
                installation.close()
            self.assertIs(app.mouse_support, original)
