"""Menus follow the button and available screen/pane space on every frame."""

import unittest

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition

from hub.widget.controls import ScrollablePane
from hub.widget.dropdown import DropdownBox
from tests.hub.support import SizedOutput


class DropdownPlacementTests(unittest.TestCase):
    def paint(self, app):
        app.layout.update_parents_relations()
        screen = Screen()
        size = app.output.get_size()
        app.layout.container.write_to_screen(screen, MouseHandlers(),
            WritePosition(0, 0, size.columns, size.rows), '', True, None)
        screen.draw_all_floats()
        return screen.visible_windows_to_write_positions

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
