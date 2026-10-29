"""Settings tabs and shared blank-space selection behavior."""

import asyncio
import tempfile
import unittest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
from prompt_toolkit.data_structures import Point
from examples.hub.preview import create_preview
from hub.backend.runtime import HubRuntime, HubConfig
from hub.ui.settings.pages import ProjectPage
from hub.widget.sidebar import SidebarList, SidebarItem
from hub.widget.tab_page import TabPage
from hub.widget.controls import TextArea
from tests.hub.support import SizedOutput, eventually


class SettingsTabTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput())
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running and self.view.composer.window.render_info)

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        self.view.close()
        self.view.progress.close()

    async def test_generic_tabs_arrows_enter_escape(self):
        first, second = TextArea(text="first"), TextArea(text="second")
        tabs = TabPage([("a", "A", first), ("b", "B", second)])
        with set_app(self.app):
            self.view.open_dialog("Tabs", tabs, focus=tabs.buttons[0])
        self.pipe.send_text("\x1b[C")
        await eventually(lambda: tabs.selected == "b")
        self.assertIs(self.app.layout.current_control, tabs.buttons[1].control)
        self.pipe.send_text(" ")
        await eventually(lambda: self.app.layout.current_control is second.control)
        self.pipe.send_text("\x1b")
        await eventually(lambda: tabs.tabs_focused)
        self.assertIsNotNone(self.view._dialog)

    async def test_settings_sidebar_enters_tab_strip_and_fields_return_to_tabs(self):
        with set_app(self.app):
            self.view.toggle_settings()
            self.view.settings.choose("appearance")
            self.app.layout.focus(self.view.settings.global_list)
        screen = self.view.settings
        for key in ("\r", " ", "\x1b[C"):
            self.pipe.send_text(key)
            await eventually(lambda: screen.tabs.tabs_focused)
            self.pipe.send_text("\r")
            await eventually(lambda: self.app.layout.current_control in [f.input.control for f in screen.page.fields()])
            self.pipe.send_text("\r")
            await eventually(lambda: screen.editing)
            self.pipe.send_text("\x1b")
            await eventually(lambda: screen.tabs.tabs_focused)
            self.assertFalse(screen.editing)
            self.pipe.send_text("\x1b")
            await eventually(lambda: self.app.layout.current_control is screen.global_list)

    async def test_project_tabs_partition_fields_without_losing_drafts_or_filter(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()
            try:
                record = await runtime.settings_service.load_project(runtime.project.id)
                screen = self.view.settings
                page = ProjectPage(screen, record, suggest_defaults=False)
                hidden = [f for form in [*page.engine_forms.values(), *page.component_forms.values()]
                          for f in form.fields.values()]
                self.assertTrue(hidden)
                self.assertTrue(all(f._input is None for f in hidden))
                original = page.values()
                self.assertFalse(page.dirty)
                self.assertTrue(all(f._input is None for f in hidden))
                page.set_section("engine:loop")
                screen.page = page
                field = page.fields()[0]
                widget = field.input
                field.input.text = "128"
                before = page.values()
                self.assertEqual(before['config']['parameters']['components'],
                                 original['config']['parameters']['components'])
                self.assertTrue(all(f._input is None for form in page.component_forms.values()
                                    for f in form.fields.values()))
                self.assertTrue(page.fields())
                self.assertTrue(all(page.field_tabs[f] == "config" for f in page.fields()))
                with set_app(self.app):
                    screen.tabs.selected = "policy"
                    screen.tab_changed("policy")
                self.assertTrue(page.fields())
                self.assertTrue(all(page.field_tabs[f] == "policy" for f in page.fields()))
                page.filter("no-match-anywhere")
                self.assertEqual(page.fields(), [])
                self.assertEqual(len(page.container.children), 1)
                self.assertEqual(page.values(), before)
                with set_app(self.app):
                    screen.tabs.selected = "config"
                    screen.tab_changed("config")
                self.assertEqual(field.input.text, "128")
                self.assertIs(field.input, widget)
                self.assertEqual(page.values(), before)
            finally:
                await runtime.close()

    async def test_empty_policy_tab_does_not_focus_hidden_global_fields(self):
        with set_app(self.app):
            self.view.toggle_settings()
            screen = self.view.settings
            screen.choose("general")
            screen.tabs.select("policy")
        self.pipe.send_text("\r ")
        await asyncio.sleep(.1)
        self.assertTrue(screen.tabs.tabs_focused)
        self.assertEqual(screen.page.body_widgets(), [])

    async def test_blank_sidebar_rows_focus_without_selection(self):
        selected = ["a"]
        control = SidebarList(lambda: [SidebarItem("a", "A", "detail"), SidebarItem("b", "B")],
                              lambda: selected[0], lambda key: selected.__setitem__(0, key))
        from prompt_toolkit.layout import Window
        with set_app(self.app):
            self.view.open_dialog("List", Window(control, height=8), focus=control)
        await eventually(lambda: any(w.content is control for w in self.app.renderer._last_screen.visible_windows))
        window = next(w for w in self.app.renderer._last_screen.visible_windows if w.content is control)
        rect = self.app.renderer._last_screen.visible_windows_to_write_positions[window]
        self.pipe.send_text(f"\x1b[<0;{rect.xpos+2};{rect.ypos+7}M\x1b[<0;{rect.xpos+2};{rect.ypos+7}m")
        await asyncio.sleep(.1)
        self.assertEqual(selected[0], "a")
        with set_app(self.app):
            control.mouse_handler(MouseEvent(Point(0, 2), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        self.assertEqual(selected[0], "a")

    def test_composer_uses_auxiliary_glyphs(self):
        from hub.widget.auxiliary import AuxiliaryButton
        actions = self.view.composer_actions
        self.assertIsInstance(actions.stop, AuxiliaryButton)
        self.assertEqual(actions.stop_label(), "")
        self.assertEqual(actions.send.text, "")
