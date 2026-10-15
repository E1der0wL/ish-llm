"""The host, pages and readers share explicit, sorted input contracts."""

import asyncio
from types import SimpleNamespace
import unittest
from tests.hub.support import open_home

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import install_preview
from hub.ui.chat.history_ui import HistoryUI
from hub.widget.header import HeaderBar
from hub.widget.output.reader import ReadOnlyDialog
from hub.widget.controls import TextArea
from hub.model import ChatMessage
from tests.hub.test_ish_integration import Prompt
from tests.hub.support import settings_button, SizedOutput, eventually, minimize


class InputContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_removed_panel_and_settings_shortcuts_leave_drafts_untouched(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install_preview(prompt)
            app, view = prompt.app, installation.view
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                for settings in (False, True):
                    with set_app(app):
                        if settings:
                            view.toggle_settings()
                            view.settings.choose("profile")
                            field = view.settings.page.form.fields[("display_name",)]
                            field.input.text = "unsaved name"
                        view.active_page.focus_sidebar()
                    pipe.send_text("\x1b")
                    await asyncio.sleep(0.12)
                    self.assertTrue(view.visible)
                    self.assertTrue(view.active_page.sidebar_focused)
                    with set_app(app):
                        self.assertNotIn("ESC", [key for key, _ in view.bindings.hints()])
                for scope in ("panel", "field", "editing", "buttons"):
                    with set_app(app):
                        if scope == "field":
                            app.layout.focus(field.input)
                        elif scope == "editing":
                            field.begin()
                        elif scope == "buttons":
                            view.settings.finish_edit()
                            app.layout.focus(view.settings.page.save)
                        focus = app.layout.current_control
                    pipe.send_text("\x13\x03")
                    await asyncio.sleep(0.12)
                    self.assertTrue(view.settings_open)
                    self.assertTrue(view.visible)
                    self.assertFalse(task.done())
                    self.assertIs(app.layout.current_control, focus)
                    self.assertEqual(field.input.text, "unsaved name")
                    self.assertTrue(view.settings.page.dirty)
                    with set_app(app):
                        hints = [key for key, _ in view.bindings.hints()]
                        self.assertNotIn("Ctrl+S", hints)
                        self.assertNotIn("Ctrl+C", hints)
                settings_button(app, pipe, view, "cancel")
                await open_home(app, view)
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_ctrl_q_minimizes_from_every_hub_input_scope(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install_preview(prompt)
            app, view = prompt.app, installation.view
            view.transcript.set_messages((ChatMessage("assistant", "```py\nprint('hello')\n```", id="code"),))
            task = asyncio.create_task(prompt.prompt_async())
            rendered = asyncio.Event()
            app.after_render += lambda _: rendered.set()
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("shell draft\x11")
                await eventually(lambda: view.visible and bool(view.transcript.control.objects))
                for scope in ("composer", "completion", "sidebar", "settings panel", "settings field",
                              "settings editing", "dialog", "help", "logs", "tags", "expanded", "expanded search",
                              "question"):
                    with self.subTest(scope=scope):
                        field = None
                        with set_app(app):
                            if view.settings_open:
                                view.toggle_settings()
                            view._focus_composer()
                            view.composer.text = "hub draft"
                            if scope == "completion":
                                view.composer.text = "/eng"
                                view.composer.buffer.cursor_position = len(view.composer.text)
                                view.composer.buffer.start_completion(select_first=False)
                            elif scope == "sidebar":
                                view._focus_sessions()
                            elif scope.startswith("settings"):
                                view.toggle_settings()
                                view.settings.choose("appearance")
                                if scope != "settings panel":
                                    field = view.settings.page.form.fields[("accent1",)]
                                    app.layout.focus(field.input)
                                    if scope == "settings editing":
                                        field.begin()
                                        field.input.text = "#112233"
                            elif scope == "dialog":
                                entry = TextArea(text="popup draft")
                                view.open_dialog("Name", entry, focus=entry)
                            elif scope == "help":
                                view.help_dialog()
                            elif scope == "logs":
                                HistoryUI(SimpleNamespace(view=view))._activity_dialog("log entry")
                            elif scope in ("tags", "expanded", "expanded search"):
                                view.tags.open()
                                if scope != "tags":
                                    view.tags.expand()
                                    if scope == "expanded search":
                                        view._dialog.start_query()
                            elif scope == "question":
                                view.question.sync("project", view.sessions[view.selected].id, ({
                                    "run_id": "run", "live": True, "request": {
                                        "id": "question", "revision": 1, "title": "Choose", "source": {"choices": ["Yes", "No"]}}},))
                                view.composer.text = "1"
                        if scope == "completion":
                            await eventually(lambda: view.composer.buffer.complete_state is not None)
                        rendered.clear()
                        app.invalidate()
                        await asyncio.wait_for(rendered.wait(), 3)
                        draft = view.composer.text
                        pipe.send_text("\x11")
                        await eventually(lambda: not view.visible)
                        self.assertFalse(task.done())
                        self.assertIs(app.current_buffer, prompt.default_buffer)
                        self.assertEqual(prompt.default_buffer.text, "shell draft")
                        self.assertEqual(view.composer.text, draft)
                        self.assertIsNone(view._dialog)
                        self.assertFalse(view.tags.visible)
                        if field:
                            self.assertFalse(field.editing)
                        pipe.send_text("\x11")
                        await eventually(lambda: view.visible)
                        self.assertEqual(view.composer.text, draft)
                        if scope == "settings editing":
                            self.assertEqual(field.input.text, "#112233")
                            self.assertTrue(view.settings.page.dirty)
                        if scope == "question":
                            self.assertIsNotNone(view.question.item)
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_shell_bindings_headers_and_shared_readers(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            shell_search = []
            prompt.set_key("c-s", handler=lambda e: shell_search.append(True))
            original_s = list(prompt.key_bindings.get_bindings_for_keys(("c-s",)))
            installation = install_preview(prompt)
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
                # The shell entry binding yields to Hub's global minimize binding.
                with set_app(app):
                    self.assertFalse(prompt.key_bindings.get_bindings_for_keys(("c-q",))[0].filter())
                    self.assertEqual([key for key, _ in view.bindings.hints()], [
                        "ESC", "Ctrl+F", "Ctrl+P", "Ctrl+Q", "Ctrl+S", "Ctrl+T", "Ctrl+V/Ctrl+Y", "Alt + 이동키"])
                    self.assertEqual(view.bindings.hints()[0], ("ESC", "패널"))
                await paint()
                chat_header = next(w for w in app.renderer._last_screen.visible_windows if isinstance(w, HeaderBar))
                pipe.send_text("\x13")
                await eventually(lambda: view.settings_open)
                await paint()
                settings_header = next(w for w in app.renderer._last_screen.visible_windows if isinstance(w, HeaderBar))
                self.assertEqual(chat_header.style, settings_header.style)
                self.assertEqual(chat_header.fragments()[0][1][:4], "   ")
                with set_app(app):
                    self.assertNotIn("ESC", [key for key, _ in view.bindings.hints()])
                await minimize(pipe, view)
                pipe.send_text("\x13")
                await eventually(lambda: len(shell_search) == 2)
                self.assertFalse(view.visible)
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                settings_button(app, pipe, view, "cancel")
                await open_home(app, view)
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
                        await eventually(lambda: popup.output._render_key is not None and
                                         popup.output._render_key == popup.output._requested_key)
                        self.assertIs(app.layout.current_control, popup.receiver)
                        with set_app(app):
                            self.assertEqual(view.bindings.hints(), [
                                ("ESC", "닫기"), ("Ctrl+L", "닫기"), ("Ctrl+Q", "최소화"),
                                ("Enter", "닫기"), ("이동키", "스크롤")])
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
