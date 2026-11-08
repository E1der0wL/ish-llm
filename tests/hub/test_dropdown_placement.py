"""Menus follow the button and available screen/pane space on every frame."""

import asyncio
import unittest
from unittest.mock import Mock

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition

from hub.widget.controls import Button, ScrollablePane
from hub.widget.dialog import Dialog, PopupSize
from examples.hub.preview import create_preview
from hub.widget.dropdown import DropdownBox, DropdownLayer
from tests.hub.support import SizedOutput, eventually


class DropdownPlacementTests(unittest.TestCase):
    def paint(self, app):
        app.layout.update_parents_relations()
        screen = Screen()
        size = app.output.get_size()
        app.layout.container.write_to_screen(screen, MouseHandlers(),
            WritePosition(0, 0, size.columns, size.rows), '', True, None)
        screen.draw_all_floats()
        return screen.visible_windows_to_write_positions

    def test_shared_overlay_uses_space_below_pane_without_moving_button(self):
        dropdown = DropdownBox([(i, str(i)) for i in range(4)], value=0)
        pane = ScrollablePane(HSplit([Window(height=8), dropdown]), height=10)
        output = SizedOutput(60, 20)
        app = Application(layout=Layout(DropdownLayer(HSplit([pane, Window()]))),
                          input=DummyInput(), output=output)
        with set_app(app):
            self.paint(app)
            dropdown.open()
            positions = self.paint(app)
            button, menu = positions[dropdown.button.window], positions[dropdown.choices.window]
            self.assertEqual(menu.ypos, button.ypos + button.height)
            self.assertEqual(menu.height, 4)
            self.assertEqual(menu.width, button.width)
            self.assertGreater(menu.ypos + menu.height, pane.viewport_height)
            self.assertEqual(pane.vertical_scroll, 0)

    def test_menu_below_above_and_clipped_in_short_terminal(self):
        for before, rows, count, below, height in (
            (2, 20, 4, True, 4), (17, 20, 4, False, 4), (7, 12, 20, False, 7),
            (2, 8, 20, True, 5),
        ):
            with self.subTest(before=before, rows=rows):
                dropdown = DropdownBox([(i, str(i)) for i in range(count)], value=0)
                body = HSplit([Window(height=before), dropdown, Window()])
                app = Application(layout=Layout(body), input=DummyInput(), output=SizedOutput(50, rows))
                with set_app(app):
                    dropdown.open()
                    positions = self.paint(app)
                button, menu = positions[dropdown.button.window], positions[dropdown.choices.window]
                self.assertEqual(menu.height, height)
                self.assertEqual(menu.xpos, button.xpos)
                self.assertEqual(menu.width, button.width)
                self.assertEqual(menu.ypos if below else menu.ypos + menu.height,
                                 button.ypos + button.height if below else button.ypos)
                self.assertGreaterEqual(menu.ypos, 0)
                self.assertLessEqual(menu.ypos + menu.height, rows)

    def test_scrolled_pane_uses_visible_rows_and_repositions_after_resize(self):
        dropdown = DropdownBox([(i, str(i)) for i in range(4)], value=0)
        pane = ScrollablePane(HSplit([Window(height=30), dropdown, Window(height=40)]))
        output = SizedOutput(60, 16)
        app = Application(layout=Layout(pane), input=DummyInput(), output=output)
        with set_app(app):
            pane.vertical_scroll = 26
            dropdown.open()
            positions = self.paint(app)
            button, menu = positions[dropdown.button.window], positions[dropdown.choices.window]
            self.assertEqual(menu.ypos, button.ypos + button.height)
            # No reset of the selection or value when the available area changes.
            dropdown.choices._selected_index = 2
            output.rows = 8
            pane.vertical_scroll = 23
            positions = self.paint(app)
            button, menu = positions[dropdown.button.window], positions[dropdown.choices.window]
            self.assertEqual(menu.ypos + menu.height, button.ypos)
            self.assertEqual(dropdown.value, 0)
            self.assertEqual(dropdown.choices._selected_index, 2)
            self.assertGreaterEqual(menu.ypos, 0)
            self.assertLessEqual(menu.ypos + menu.height, output.rows)


class DropdownMouseTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_and_modal_menus_close_outside_and_keep_selection_inside(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput(122, 37))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))

            async def paint():
                before = app.render_counter
                app.invalidate()
                await eventually(lambda: app.render_counter > before)
                return app.renderer._last_screen.visible_windows_to_write_positions

            async def click(x, y):
                pipe.send_text(f'\x1b[<0;{x+1};{y+1}M\x1b[<0;{x+1};{y+1}m')
                await asyncio.sleep(.05)
                await paint()

            try:
                await eventually(lambda: app.is_running and view.visible)
                with set_app(app):
                    view.toggle_settings()
                    view.settings.choose('appearance')
                    field = view.settings.page.form.fields[('icon_style',)]
                    menu = field.input
                    app.layout.focus(menu.control)
                    await paint()
                    original = field.text
                    menu.open()
                    positions = await paint()
                    anchor, choices = positions[menu.button.window], positions[menu.choices.window]
                    self.assertEqual(choices.ypos, anchor.ypos + anchor.height)
                    await click(anchor.xpos + 3, 3)  # Noneditable page heading.
                    self.assertFalse(menu.expanded)
                    self.assertEqual(field.text, original)

                    changed = Mock()
                    menu = DropdownBox([('a', 'A'), ('b', 'B')], value='a', on_change=changed)
                    popup = Dialog(title='Menu', body=menu, size=PopupSize.MEDIUM,
                                   buttons=[Button('Close', handler=view.close_dialog)])
                    view.dialogs.show(popup, menu.control)
                    await paint()
                    menu.open()
                    await paint()
                    await click(0, 0)  # Outside the modal; must not click through.
                    self.assertFalse(menu.expanded)
                    self.assertIs(view._dialog, popup)
                    changed.assert_not_called()
                    menu.open()
                    positions = await paint()
                    choices = positions[menu.choices.window]
                    await click(choices.xpos + 2, choices.ypos + 1)
                    changed.assert_called_once_with('b')
                    self.assertFalse(menu.expanded)
                    self.assertIs(view._dialog, popup)
            finally:
                app.exit()
                await task
