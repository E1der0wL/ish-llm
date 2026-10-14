from tests.hub.support import settings_button, minimize
"""Assert visible renderer cells, not only popup state and key handling."""

import asyncio
import unittest

from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import install_preview
from tests.hub.test_ish_integration import Prompt
from tests.hub.support import settings_button, SizedOutput, eventually


def screen_text(app, columns, rows):
    screen = app.renderer._last_screen
    if screen is None:
        return ""
    return "\n".join("".join(screen.data_buffer[y][x].char for x in range(columns))
                     for y in range(rows))


class OverlayTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_popups_are_visible_after_settings_and_resize(self):
        with create_pipe_input() as pipe:
            output = SizedOutput()
            prompt = Prompt(input=pipe, output=output)
            installation = install_preview(prompt)
            view, app = installation.view, prompt.app
            view.engines = ("loop", "review-overlay")
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("shell draft\x11")
                await eventually(lambda: view.visible)
                for columns, rows in ((132, 40), (80, 24)):
                    output.columns, output.rows = columns, rows
                    app._on_resize()
                    pipe.send_text("\x13")
                    await eventually(lambda: view.settings_open)
                    settings_button(app, pipe, view, "cancel")
                    await eventually(lambda: not view.settings_open)
                    pipe.send_text("/engine ")
                    await eventually(lambda: view.composer.buffer.complete_state is not None)
                    await eventually(lambda: "review-overlay" in screen_text(app, columns, rows))
                    pipe.send_text("\x05")
                    await asyncio.sleep(.05)
                    self.assertIsNone(view._dialog)
                    view.composer.text = ""
                    pipe.send_text("/help\r")
                    await eventually(lambda: view._dialog is not None)
                    await eventually(lambda: view.t("help_title") in screen_text(app, columns, rows))
                    self.assertIn(view.t("history_close"), screen_text(app, columns, rows))
                    pipe.send_text("\r")
                    await eventually(lambda: view._dialog is None)
                    view.composer.text = ""
                await minimize(pipe, view)
                await eventually(lambda: not view.visible)
                self.assertEqual(prompt.default_buffer.text, "shell draft")
            finally:
                if not task.done():
                    app.exit(result="")
                await task
                installation.close()
