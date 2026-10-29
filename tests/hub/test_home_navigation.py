"""Home title, category-only Tab navigation and the shared profile footer."""

import asyncio
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from tests.hub.support import SizedOutput, eventually
from hub.asset.icon import AuxiliaryIcon
from hub.ui.input.keys import KeyName
from hub.widget.dialog import PopupSize
from hub.backend.project.settings_contracts import SettingsOperation


class HomeNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_category_actions_render_and_name_editing_stays_small(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput(100, 28))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                view.project_id = view.projects.selected = 'p'
                view.projects.items = [{'id': 'p', 'title': 'Project'}]
                view.projects.call = Mock()
                view.on_rename = Mock()
                for bar in (view.project_actions, view.session_buttons):
                    self.assertEqual(tuple(bar.buttons), tuple(name for name, _ in bar.ACTIONS))
                    with set_app(app):
                        app.layout.focus(bar.buttons[AuxiliaryIcon.EDIT])
                        app.invalidate()
                    frame = app.render_counter
                    await eventually(lambda: app.render_counter > frame)
                    self.assertTrue(view.chat_page.sidebar_focused)
                    positions = app.renderer._last_screen.visible_windows_to_write_positions
                    edge = positions[view.chat_page.edge].xpos
                    self.assertTrue(all(positions[b.window].xpos + positions[b.window].width <= edge
                                        for b in bar.buttons.values()))
                    pipe.send_text('\r')
                    await eventually(lambda: view._dialog is not None)
                    self.assertEqual(view._dialog.size, PopupSize.SMALL)
                    with set_app(app):
                        self.assertEqual((view._dialog._width().preferred, view._dialog._height().preferred), (60, 9))
                    field = app.current_buffer
                    field.text = ''
                    pipe.send_text('\r')
                    await asyncio.sleep(.05)
                    self.assertIsNotNone(view._dialog)
                    field.text = 'Renamed'
                    pipe.send_text('\r')
                    await eventually(lambda: view._dialog is None)
                    self.assertIs(app.layout.current_control, bar.buttons[AuxiliaryIcon.EDIT].control)
                self.assertEqual(view.projects.call.call_args.args,
                                 (SettingsOperation.RENAME_PROJECT, 'p', 'Renamed'))
                view.on_rename.assert_called_once_with(view.sessions[view.selected].id, 'Renamed')
                with set_app(app):
                    view.projects.perform(KeyName.C)
                    self.assertEqual(view._dialog.size, PopupSize.MEDIUM)
                    view.close_dialog()
                    view.session_actions.create()
                    self.assertEqual(view._dialog.size, PopupSize.MEDIUM)
                    view.close_dialog()
                    for perform, key in ((view.projects.perform, KeyName.R),
                                         (view.projects.perform, KeyName.CLEAR),
                                         (view.session_actions.perform, KeyName.CLEAR)):
                        perform(key)
                        self.assertEqual(view._dialog.size, PopupSize.SMALL)
                        self.assertEqual((view._dialog._width().preferred,
                                          view._dialog._height().preferred), (60, 9))
                        view.close_dialog()
            finally:
                app.exit()
                await task
                view.close()

    def test_category_actions_share_existing_crud_handlers_in_order(self):
        from hub.widget.sidebar import SidebarActions
        from hub.asset.icon import NERD
        perform = Mock()
        bar = SidebarActions(perform, lambda: NERD, lambda: None)
        for button in bar.buttons.values():
            button.handler()
        self.assertEqual([call.args[0] for call in perform.call_args_list],
                         [KeyName.C, KeyName.R, KeyName.EDIT, KeyName.CLEAR])

    async def test_page_switch_keeps_shortcuts_before_next_render(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("\x11")
                await eventually(lambda: app.renderer._last_screen is not None and view.composer.window in
                                 app.renderer._last_screen.visible_windows_to_write_positions)
                # Input can arrive between two render ticks. Page switches must
                # update key-binding ancestry without relying on a later paint.
                with patch.object(app, "_redraw"):
                    pipe.send_text("\x10\r")
                    await eventually(lambda: view.settings_open)
                    pipe.send_text("\x10\r")
                    await eventually(lambda: not view.settings_open)
                    pipe.send_text("\x10\x1b[C")
                    await eventually(lambda: app.layout.current_control is view.profile_bar.presets.control)
                    pipe.send_text("\x11")
                    await eventually(lambda: not view.visible)
            finally:
                app.exit()
                await task
                view.close()

    async def test_categories_profile_buttons_and_shell_restore(self):
        with create_pipe_input() as pipe:
            output = SizedOutput()
            app, view, shell = create_preview(input=pipe, output=output)
            task = asyncio.create_task(app.run_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("shell draft\x11")
                await eventually(lambda: view.visible)
                view.project_title = "Renamed project"
                self.assertEqual(view.header.fragments()[0][1], "  홈")
                view.project_id = "p"
                view.projects.selected = "p"
                view.projects.items = [{"id": "p", "title": "Project"}]
                pipe.send_text("\x1b")
                await eventually(lambda: app.layout.current_control is view._session_control)
                pipe.send_text("\x1b[A")
                await asyncio.sleep(.06)
                self.assertIs(app.layout.current_control, view._session_control)
                pipe.send_text("\t")
                await eventually(lambda: app.layout.current_control is view.projects.control)
                pipe.send_text("\x1b[A\x1b[B")
                await asyncio.sleep(.06)
                self.assertIs(app.layout.current_control, view.projects.control)
                pipe.send_text("\x1b[Z")
                await eventually(lambda: app.layout.current_control is view._session_control)
                pipe.send_text("\x1b[C\x10")
                await eventually(lambda: app.layout.current_control is view.profile_bar.page.control)
                self.assertEqual(view.profile_bar.page.text, "설정")
                for sequence in ("\t", "\x1b[Z", "\x1b[A", "\x1b[B", "\x1b[D"):
                    pipe.send_text(sequence)
                    await asyncio.sleep(.05)
                    self.assertIs(app.layout.current_control, view.profile_bar.page.control)
                for width in (19, 24, 39):
                    with set_app(app):
                        view.settings.set_sidebar_width(width)
                    app.invalidate()
                    await asyncio.sleep(.08)
                    positions = app.renderer._last_screen.visible_windows_to_write_positions
                    left = positions[view.profile_bar.page.window]
                    right = positions[view.profile_bar.presets.button.window]
                    edge = positions[view.chat_page.edge].xpos
                    self.assertLessEqual(abs(left.xpos - (edge - view.sidebar_width()) -
                                             (edge - right.xpos - right.width)), 1)
                pipe.send_text(" ")
                await eventually(lambda: view.settings_open)
                self.assertEqual(view.profile_bar.page.text, "홈")
                with set_app(app):
                    view.settings.choose("general")
                    app.layout.focus(view.settings.global_list)
                pipe.send_text("\x1b[B")
                await asyncio.sleep(.08)
                self.assertIs(app.layout.current_control, view.settings.global_list)
                self.assertEqual(view.settings.selected, "debugging")
                pipe.send_text("\t")
                await eventually(lambda: app.layout.current_control is view.settings.project_list)
                pipe.send_text("\x1b[A")
                await asyncio.sleep(.08)
                self.assertIs(app.layout.current_control, view.settings.project_list)
                pipe.send_text("\x1b[Z")
                await eventually(lambda: app.layout.current_control is view.settings.global_list)
                pipe.send_text("\x10\r")
                await eventually(lambda: not view.settings_open)
                pipe.send_text("\x10\x1b[C")
                await eventually(lambda: app.layout.current_control is view.profile_bar.presets.control)
                pipe.send_text("\t\x1b[C")
                await asyncio.sleep(.05)
                self.assertIs(app.layout.current_control, view.profile_bar.presets.control)
                pipe.send_text("\x1b[D")
                await eventually(lambda: app.layout.current_control is view.profile_bar.page.control)
                pipe.send_text("\x1b[C\x11")
                await eventually(lambda: not view.visible)
                self.assertIs(app.layout.current_control, shell.control)
                self.assertEqual(shell.text, "shell draft")
            finally:
                app.exit()
                await task
                view.close()
                view.progress.close()
