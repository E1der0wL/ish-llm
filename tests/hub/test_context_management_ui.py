import unittest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput

from examples.hub.preview import create_preview
from hub.config.theme_style import ThemeSelector
from hub.locales.keys import MessageKey
from hub.widget.output.reader import ReadOnlyDialog
from tests.hub.support import SizedOutput


class ContextManagementUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput(132, 40))
        self.enterContext(set_app(self.app))

    async def asyncTearDown(self):
        self.view.close()

    def paint_home(self):
        self.app.renderer.render(self.app, self.app.layout)
        return self.app.renderer._last_screen

    async def test_home_search_visible_through_full_layout(self):
        self.view.show(self.app)
        self.view.output.search.open()
        search = self.view.output.search
        for columns, rows in ((132, 40), (80, 24), (48, 20)):
            with self.subTest(columns=columns):
                self.app.output.columns, self.app.output.rows = columns, rows
                screen = self.paint_home()
                self.assertIn(search.input.window, screen.visible_windows_to_write_positions)
                rect = screen.visible_windows_to_write_positions[search.input.window]
                self.assertEqual(rect.height, 1)
                self.assertGreater(rect.width, 10)
                self.assertIs(self.app.layout.current_control, search.input.control)
                text = '\n'.join(''.join(screen.data_buffer[y][x].char for x in range(columns))
                                 for y in range(rows))
                self.assertIn(self.view.t(MessageKey.SEARCH_TITLE), text)

    async def test_reader_background_covers_empty_and_populated_cells(self):
        self.view.show(self.app)
        for text in ('', 'No recorded history', 'Log line\n' * 80):
            popup = ReadOnlyDialog('Activity', text, self.view._rows, self.view._columns,
                                   self.view.close_dialog, self.view.t)
            self.view.dialogs.show(popup, popup.receiver)
            screen = self.paint_home()
            rect = screen.visible_windows_to_write_positions[popup.output.window]
            for dy in range(rect.height):
                for dx in (0, rect.width // 2, rect.width - 2):
                    cell = screen.data_buffer[rect.ypos + dy][rect.xpos + dx]
                    attrs = self.view.style.get_attrs_for_style_str(cell.style)
                    self.assertEqual(attrs.bgcolor, self.view.theme.accent5[1:])
            self.view.close_dialog()

    def test_focused_button_and_footer_key_use_accent_two(self):
        for style in ('class:hub class:button.focused',
                      'class:hub class:button.focused class:button.text',
                      ThemeSelector.SHORTCUT_KEY.style):
            attrs = self.view.style.get_attrs_for_style_str(style)
            self.assertEqual(attrs.color, self.view.theme.accent2[1:])
        self.assertEqual(self.view.t(MessageKey.SHORTCUT_ALT_MOVEMENT), 'Alt + ←↓↑→')
