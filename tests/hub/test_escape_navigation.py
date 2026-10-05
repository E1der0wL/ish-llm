from tests.hub.test_mockup import minimize
"""ESC navigation must not swallow Alt sequences or interrupt a Run."""

import asyncio
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.hub import install
from tests.hub.test_ish_integration import Prompt
from tests.hub.test_mockup import SizedOutput, eventually


class EscapeNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_stop_alt_scope_and_host_timeouts(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            app = prompt.app
            app.ttimeoutlen, app.timeoutlen = 0.4, 1.2
            installation = install(prompt, preview=True)
            view = installation.view
            interrupts = []
            view.on_interrupt = interrupts.append
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: app.is_running)
                self.assertEqual((app.ttimeoutlen, app.timeoutlen), (0.4, 1.2))
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                self.assertEqual((app.ttimeoutlen, app.timeoutlen), (0.02, 0.02))
                view.composer.text = "draft"
                pipe.send_text("\x1b")
                await eventually(lambda: app.layout.current_control == view._session_control)
                self.assertEqual(interrupts, [])
                pipe.send_text("\t\x18")
                await eventually(lambda: len(interrupts) == 1)
                self.assertIs(app.layout.current_control, view.composer.control)
                pipe.send_text("\x1b\x1b[B")
                await eventually(lambda: view.transcript.control.top_line == 1)
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertEqual(len(interrupts), 1)
                pipe.send_text("\x13\t")
                await eventually(lambda: view.settings_open and not view.settings.layout.sidebar_focused)
                sequences = ("\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D", "\x1b[5~", "\x1b[6~", "\x1b[H", "\x1b[F")
                for editing in (False, True):
                    if editing:
                        pipe.send_text("\r")
                        await eventually(lambda: view.settings.editing)
                    control = app.layout.current_control
                    selected = view.settings.selected
                    top = view.transcript.control.top_line
                    pipe.send_text("".join("\x1b" + sequence for sequence in sequences))
                    await asyncio.sleep(0.1)
                    self.assertTrue(view.settings_open)
                    self.assertIs(app.layout.current_control, control)
                    self.assertEqual(view.settings.selected, selected)
                    self.assertEqual(view.transcript.control.top_line, top)
                pipe.send_text("\x1b")
                await eventually(lambda: view.settings.layout.sidebar_focused)
                self.assertFalse(view.settings.editing)
                self.assertEqual(view.composer.text, "draft")
                await minimize(pipe, view)
                await eventually(lambda: not view.visible)
                self.assertEqual((app.ttimeoutlen, app.timeoutlen), (0.4, 1.2))
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                installation.close()
                self.assertEqual((app.ttimeoutlen, app.timeoutlen), (0.4, 1.2))
            finally:
                if not task.done():
                    app.exit(result="")
                await task
                installation.close()
