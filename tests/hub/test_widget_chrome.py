"""Exercise contextual chrome and icon preferences with the real host dispatcher."""

import asyncio
from dataclasses import asdict, replace
import tempfile
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.utils import get_cwidth

from hub.asset.icon import NERD, UNICODE
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.preferences import PreferencesStore
from hub.config.theme import HubTheme
from hub.hub import install
from hub.locales import Language
from hub.model import ChatMessage
from hub.widget.reader import ReadOnlyDialog
from hub.widget.conversation import render_messages
from hub.widget.controls import RadioList
from hub.widget.settings import ChoiceField
from tests.hub.test_ish_integration import Prompt
from tests.hub.test_mockup import SizedOutput, eventually


class WidgetChromeTests(unittest.IsolatedAsyncioTestCase):
    async def test_focused_footers_and_icon_choice_dispatch(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            rendered = asyncio.Event()
            app.after_render += lambda _: rendered.set()
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))

            def keys():
                with set_app(app):
                    return dict(view.bindings.hints())

            async def paint():
                rendered.clear()
                app.invalidate()
                await asyncio.wait_for(rendered.wait(), 3)

            try:
                await eventually(lambda: app.is_running and view.transcript.window.render_info is not None)
                self.assertIn("Ctrl+E", keys())
                self.assertNotIn("c", keys())
                self.assertEqual(view._header()[0][1], " •  demo-project")
                # Keep the input help, remove only the separate sidebar hint row.
                self.assertEqual(len(view._sidebar.content.children), 2)
                self.assertEqual(len(view._composer_frame.body.children), 3)
                self.assertFalse(view.question.container.filter())
                pipe.send_text("\x1b")
                await eventually(lambda: app.layout.current_control == view._session_control)
                self.assertTrue({"c", "d", "e", "r", "Ctrl+←→", "Tab/Space/Enter/→"} <= keys().keys())
                self.assertNotIn("Ctrl+Space", keys())

                pipe.send_text("\x13")
                screen = view.settings
                await eventually(lambda: view.settings_open and screen.page is not None)
                await paint()
                self.assertNotIn("c", keys())
                self.assertNotIn("Ctrl+E", keys())
                self.assertEqual(len(screen._left.children), 5)
                content = app.renderer._last_screen
                drawn = "\n".join("".join(content.data_buffer[y][x].char for x in range(132)) for y in range(40))
                self.assertNotIn(view.t("settings_main"), drawn)
                self.assertIn("•", drawn.splitlines()[0])
                with set_app(app):
                    screen.choose("appearance")
                    field = screen.page.form.fields[("icon_style",)]
                    app.layout.focus(field.input)
                self.assertIsInstance(field, ChoiceField)
                self.assertNotIn("e", keys())
                pipe.send_text("\r")
                await eventually(lambda: field.editing)
                self.assertEqual(keys()["Enter"], view.t("shortcut_done"))
                self.assertNotIn("Ctrl+Space", keys())
                pipe.send_text("\x1b[B")
                await eventually(lambda: field.value() == "unicode")
                pipe.send_text("\r")
                await eventually(lambda: not field.editing)
                self.assertTrue(screen.page.dirty)
                self.assertEqual(screen.page.form.values()["icon_style"], "unicode")
                await paint()
                with set_app(app):
                    screen.layout.focus_main()
                    app.layout.focus(screen.page.form.fields[("accent1",)].input)
                self.assertIn("e", keys())
                pipe.send_text("\r")
                await eventually(lambda: screen.editing)
                self.assertIn("Ctrl+Space", keys())
                self.assertNotIn("e", keys())
                pipe.send_text("\t")
                await eventually(lambda: app.layout.current_control == screen.page.save.control)
                self.assertEqual(keys()["Enter/Space"], view.t("shortcut_activate"))
                self.assertNotIn("e", keys())

                with set_app(app):
                    view.toggle_settings()
                    view.search.open()
                self.assertIn("Alt+P", keys())
                self.assertNotIn("Ctrl+S", keys())
                pipe.send_text("\t")
                await eventually(lambda: app.layout.current_control != view.search.input.control)
                self.assertIn("Alt+P", keys())
                self.assertEqual(keys()["Enter/Space"], view.t("shortcut_activate"))
                with set_app(app):
                    view.close_dialog()
                    choices = RadioList([("a", "A"), ("b", "B")])
                    view.open_dialog("Pick", choices, focus=choices)
                self.assertIn("Enter/Space", keys())
                with set_app(app):
                    view.close_dialog()
                    popup = ReadOnlyDialog("Activity", "line", view._rows, view._columns, view.close_dialog, view.t)
                    view.dialogs.show(popup, popup.receiver)
                self.assertEqual(set(keys()), {"ESC", "Ctrl+L", "Enter", "이동키"})
                pipe.send_text("\x0c")
                await eventually(lambda: view._dialog is None)
                with set_app(app):
                    view.set_theme(replace(view.theme, icon_style="unicode"))
                    self.assertEqual(view._header()[0][1], " •  demo-project")
                    for width in (20, 80, 132):
                        bar = view.shortcut_bar()
                        bar.width_available = lambda: width
                        self.assertLessEqual(get_cwidth("".join(text for _, text in bar.fragments())), width)
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_icon_persistence_isolation_and_existing_alerts(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            runtime = HubRuntime(HubConfig(directory))
            await runtime.settings_service.save_global("appearance", asdict(HubTheme(icon_style="unicode")))
            self.assertIs(runtime.t.icons, NERD)  # Rendering style belongs to the UI.
            self.assertEqual(PreferencesStore(directory).load()["appearance"]["icon_style"], "unicode")
            self.assertEqual(PreferencesStore(directory).load()["appearance"]["icon_style"], "unicode")
            # A second view/language retains its own style; changing a theme has no globals.
            self.assertIs(Language().icons, NERD)
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, preview=True)
            view = installation.view
            with set_app(prompt.app):
                view.toasts.push("Session", "failed", "session")
                self.assertIn(NERD.alerts["error"], view.toasts.text())
                view.set_theme(replace(view.theme, icon_style="unicode"))
                output = view.toasts.text() + view.t("sessions") + view.t("reasoning")
                output += "".join(text for row in render_messages(
                    (ChatMessage("reasoning", "Reasoning summary", status="running"),), 80, view.theme)
                                  for _, text in row)
                self.assertIn(UNICODE.alerts["error"], output)
                self.assertFalse(any(0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0x10FFFD for c in output))
            installation.close()
