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
from examples.hub.preview import install_preview
from hub.locales import Language
from hub.model import ChatMessage
from hub.widget.output.reader import ReadOnlyDialog
from hub.widget.transcript.conversation import render_messages
from hub.widget.controls import RadioList
from hub.widget.forms.settings import ChoiceField
from tests.hub.test_ish_integration import Prompt
from tests.hub.support import SizedOutput, eventually, render_container


class WidgetChromeTests(unittest.IsolatedAsyncioTestCase):
    async def test_focused_footers_and_icon_choice_dispatch(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install_preview(prompt)
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
                await eventually(lambda: app.is_running and view.output.transcript.window.render_info is not None)
                self.assertNotIn("Ctrl+E", keys())
                self.assertNotIn("c", keys())
                self.assertEqual(view.header.fragments()[0][1], "  홈")
                # Keep the input help, remove only the separate sidebar hint row.
                self.assertEqual(len(view._sidebar.content.sections), 2)
                self.assertIs(view._sidebar.content.children[-1], view.profile_bar)
                # Input help has its own row inside the unbroken bottom border.
                content = app.renderer._last_screen
                rect = content.visible_windows_to_write_positions[view.composer.window]
                hint = "".join(content.data_buffer[rect.ypos + rect.height][x].char
                               for x in range(rect.xpos, rect.xpos + rect.width))
                self.assertIn(view.t("input_hint"), hint)
                self.assertEqual(content.data_buffer[rect.ypos + rect.height][rect.xpos - 1].char, "│")
                self.assertEqual(content.data_buffer[rect.ypos + rect.height + 1][rect.xpos - 1].char, "└")
                self.assertFalse(view.question.container.filter())
                pipe.send_text("\x1b")
                await eventually(lambda: app.layout.current_control == view._session_control)
                self.assertTrue({"c", "d", "e", "r", "Ctrl+←→", "Tab/Shift+Tab", "Space/Enter/→"} <= keys().keys())
                self.assertNotIn("Ctrl+Space", keys())

                pipe.send_text('\x10\r')
                screen = view.settings
                await eventually(lambda: view.settings_open and screen.page is not None)
                await paint()
                self.assertNotIn("c", keys())
                self.assertNotIn("Ctrl+E", keys())
                self.assertEqual(len(screen._left.sections), 2)
                self.assertIs(screen._left.children[-1], view.profile_bar)
                content = app.renderer._last_screen
                drawn = "\n".join("".join(content.data_buffer[y][x].char for x in range(132)) for y in range(40))
                self.assertNotIn(view.t("settings_main"), drawn)
                self.assertIn("", drawn.splitlines()[0])
                self.assertIn(f"{NERD.settings} 전역 설정", drawn)
                self.assertIn(f"{NERD.project} 프로젝트", drawn)
                for heading in (f"{NERD.settings} 전역 설정", f"{NERD.project} 프로젝트"):
                    self.assertTrue(next(row for row in drawn.splitlines() if heading in row).startswith(" " + heading))
                positions = content.visible_windows_to_write_positions
                field = screen.page.form.fields[("display_name",)].input.window
                self.assertEqual(positions[field].xpos, positions[screen.layout.edge].xpos + 1)
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
                    view.output.search.open()
                self.assertIn("Alt+P", keys())
                self.assertNotIn("Ctrl+S", keys())
                pipe.send_text("\t")
                await eventually(lambda: app.layout.current_control != view.output.search.input.control)
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
                self.assertEqual(set(keys()), {"ESC", "Ctrl+L", "Ctrl+Q", "Ctrl+W", "Enter", "이동키"})
                pipe.send_text("\x0c")
                await eventually(lambda: view._dialog is None)
                with set_app(app):
                    view.set_theme(replace(view.theme, icon_style="unicode"))
                    self.assertEqual(view.header.fragments()[0][1], "•  홈")
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
            installation = install_preview(prompt)
            view = installation.view
            with set_app(prompt.app):
                view.toasts.push("Session", "failed", "session")
                def alert_text():
                    return "\n".join(render_container(view.toasts.container, view.toasts.width(), view.toasts.height()))
                self.assertIn(NERD.alerts["error"], alert_text())
                view.set_theme(replace(view.theme, icon_style="unicode"))
                output = alert_text() + view.t("sessions") + view.t("reasoning")
                output += "".join(text for row in render_messages(
                    (ChatMessage("reasoning", "Reasoning summary", status="running"),), 80, view.theme)
                                  for _, text in row)
                self.assertIn(UNICODE.alerts["error"], output)
                self.assertFalse(any(0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0x10FFFD for c in output))
            installation.close()
