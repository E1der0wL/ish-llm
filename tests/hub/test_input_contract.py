"""The host, pages and readers share explicit, sorted input contracts."""

import asyncio
from types import SimpleNamespace
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.hub import install
from hub.ui.chat.history_ui import HistoryUI
from hub.widget.header import HeaderBar
from hub.widget.reader import ReadOnlyDialog
from tests.hub.test_ish_integration import Prompt
from tests.hub.test_mockup import SizedOutput, eventually, minimize


class InputContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_bindings_headers_and_shared_readers(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            shell_search = []
            prompt.set_key("c-s", handler=lambda e: shell_search.append(True))
            original_s = list(prompt.key_bindings.get_bindings_for_keys(("c-s",)))
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            task = asyncio.create_task(prompt.prompt_async())
            rendered = asyncio.Event()
            app.after_render += lambda _: rendered.set()

            async def paint():
                rendered.clear()
                app.invalidate()
                await asyncio.wait_for(rendered.wait(), 3)

            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("\x13")
                await eventually(lambda: bool(shell_search))
                self.assertFalse(view.visible)
                self.assertEqual(prompt.key_bindings.get_bindings_for_keys(("c-s",)), original_s)
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                # Entry binding is inactive inside Hub and not a minimize action.
                with set_app(app):
                    self.assertFalse(prompt.key_bindings.get_bindings_for_keys(("c-q",))[0].filter())
                    self.assertEqual([key for key, _ in view.bindings.hints()], [
                        "ESC", "Ctrl+C", "Ctrl+E", "Ctrl+F", "Ctrl+R", "Ctrl+S", "Ctrl+T", "Alt + 이동키"])
                    self.assertEqual(view.bindings.hints()[0], ("ESC", "패널"))
                await paint()
                chat_header = next(w for w in app.renderer._last_screen.visible_windows if isinstance(w, HeaderBar))
                pipe.send_text("\x13")
                await eventually(lambda: view.settings_open)
                await paint()
                settings_header = next(w for w in app.renderer._last_screen.visible_windows if isinstance(w, HeaderBar))
                self.assertEqual(chat_header.style, settings_header.style)
                self.assertEqual(chat_header.fragments()[0][1][:4], " •  ")
                with set_app(app):
                    self.assertEqual(view.bindings.hints()[0], ("ESC", "최소화"))
                await minimize(pipe, view)
                pipe.send_text("\x13")
                await eventually(lambda: len(shell_search) == 2)
                self.assertFalse(view.visible)
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                pipe.send_text("\x13")
                await eventually(lambda: not view.settings_open)
                composer = app.layout.current_control
                view.composer.text = "preserved input"
                history = HistoryUI(SimpleNamespace(view=view))
                for kind in ("help", "logs"):
                    for close_key in ("\r", "\x1b", "\x0c"):
                        with set_app(app):
                            if kind == "help":
                                view.help_dialog()
                            else:
                                history._activity_dialog("\n".join(f"row {i} " + "x" * 150 for i in range(140)))
                        popup = view._dialog
                        self.assertIsInstance(popup, ReadOnlyDialog)
                        self.assertFalse(popup.output.is_focusable())
                        await paint()
                        self.assertIs(app.layout.current_control, popup.receiver)
                        with set_app(app):
                            self.assertEqual(view.bindings.hints(), [
                                ("ESC", "닫기"), ("Ctrl+L", "닫기"), ("Enter", "닫기"), ("이동키", "스크롤")])
                        if kind == "help":
                            self.assertTrue(popup.output.markdown)
                            text = "\n".join(popup.output.lines)
                            self.assertNotIn("## ", text)
                            self.assertNotIn("**", text)
                            self.assertTrue(any("bold" in style for row in popup.output.fragments for style, _ in row))
                        # Every reader supports the same eight Alt navigation keys.
                        pipe.send_text("\x1b[H")
                        await eventually(lambda: popup.output.top_line == 0)
                        for sequence in ("\x1b[B", "\x1b[A", "\x1b[C", "\x1b[D", "\x1b[6~", "\x1b[5~"):
                            pipe.send_text(sequence)
                            await paint()
                            self.assertIs(app.layout.current_control, popup.receiver)
                        pipe.send_text("\x1b[F")
                        await eventually(lambda: popup.output.top_line == max(0, len(popup.output.lines) - popup.output._height))
                        pipe.send_text(close_key)
                        await eventually(lambda: view._dialog is None)
                        self.assertIs(app.layout.current_control, composer)
                        self.assertEqual(view.composer.text, "preserved input")
            finally:
                app.exit(result="")
                await task
                installation.close()
                self.assertEqual(prompt.key_bindings.get_bindings_for_keys(("c-s",)), original_s)
