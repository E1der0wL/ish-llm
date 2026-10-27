"""Popup surfaces own their styling without changing the surrounding pages."""

import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.margins import ConditionalMargin
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition, Char

from examples.hub.preview import create_preview
from hub.widget.controls import Button, CheckboxList, RadioList, TextArea, ScrollbarMargin, CompletionsMenu, ScrollablePane
from hub.config.theme_style import ThemeSelector
from hub.widget.dialog import Dialog, PopupSize
from hub.widget.dropdown import CompletionChoices
from hub.widget.forms.component_manager import ComponentManager
from hub.widget.section import Heading
from tests.hub.support import SizedOutput


class PopupSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput(132, 40))
        self.enterContext(set_app(self.app))

    async def asyncTearDown(self):
        self.view.close()

    def paint(self, popup):
        width, height = popup._width().preferred, popup._height().preferred
        screen = Screen()
        for y in range(height + 2):
            for x in range(width + 2):
                screen.data_buffer[y][x] = Char('X', 'bg:#123456')
        to_container(popup).write_to_screen(screen, MouseHandlers(),
            WritePosition(1, 1, width, height), 'class:hub', False, 300)
        screen.draw_all_floats()
        return screen, width, height

    def attrs(self, screen, window, *, dx=0, dy=0):
        rect = screen.visible_windows_to_write_positions[window]
        return self.view.style.get_attrs_for_style_str(screen.data_buffer[rect.ypos + dy][rect.xpos + dx].style)

    async def test_popup_controls_fill_their_allocations_and_headings_are_accented(self):
        choices = [CheckboxList([(i, f'Item {i}') for i in range(12)]),
                   RadioList([(i, f'Option {i}') for i in range(12)])]
        for item in choices:
            item.window.height = 3
        field = TextArea(text='Example', height=2)
        heading = Heading('Field name')
        popup = Dialog(body=HSplit([heading, *choices, field]), buttons=[Button('Close')])
        self.view.dialogs.show(popup, choices[0])
        screen, _, _ = self.paint(popup)
        for window in [*(item.window for item in choices), field.window]:
            rect = screen.visible_windows_to_write_positions[window]
            # Include empty trailing cells, not just styled text fragments.
            for dy in range(rect.height):
                for dx in (0, rect.width // 2):
                    self.assertEqual(self.attrs(screen, window, dx=dx, dy=dy).bgcolor,
                                     self.view.theme.accent5[1:])
        attrs = self.attrs(screen, heading.window)
        self.assertTrue(attrs.bold)
        self.assertEqual(attrs.color, self.view.theme.accent1[1:])

    async def test_popup_sidebar_fill_does_not_change_home_or_settings_sidebar(self):
        popup = ComponentManager(self.view, lambda *a, **k: None, name='rag')
        self.view.dialogs.show(popup, popup.list)
        screen, _, _ = self.paint(popup)
        window = next(w for w in screen.visible_windows_to_write_positions if w.content is popup.list)
        self.assertEqual(self.attrs(screen, window).bgcolor, self.view.theme.accent5[1:])
        for style in ('class:hub class:hub.sidebar', 'class:hub class:hub.sidebar-heading'):
            self.assertEqual(self.view.style.get_attrs_for_style_str(style).bgcolor, 'default')

    async def test_popup_clears_previous_layer_in_all_blank_cells(self):
        for size in PopupSize:
            with self.subTest(size=size):
                field = TextArea(text='Content', height=1)
                popup = Dialog(body=field, size=size, buttons=[Button('Close')])
                self.view.dialogs.show(popup, field)
                for body in (field, Window(height=1)):
                    popup.body = body
                    screen, width, height = self.paint(popup)
                    self.assertFalse(any(screen.data_buffer[y][x].char == 'X'
                        for y in range(1, height + 1) for x in range(1, width + 1)))
                    self.assertEqual(screen.data_buffer[0][0].char, 'X')
                    self.assertEqual(screen.data_buffer[height + 1][width + 1].style, 'bg:#123456')

    async def test_all_list_and_completion_scrollbars_share_hub_geometry(self):
        radio = RadioList([('a', 'Alpha')])
        checks = CheckboxList([('a', 'Alpha')])
        completion = CompletionsMenu()
        dropdown = CompletionChoices([('a', 'Alpha')], 16)
        field = TextArea(scrollbar=True)
        for window in (radio.window, checks.window, completion.content, dropdown.window, field.window):
            for margin in window.right_margins:
                original = margin
                while isinstance(margin, ConditionalMargin):
                    margin = margin.margin
                self.assertIsInstance(margin, ScrollbarMargin)
                if isinstance(original, ConditionalMargin):
                    radio.show_scrollbar = checks.show_scrollbar = False
                    self.assertEqual(original.get_width(lambda: None), 0)
                    radio.show_scrollbar = checks.show_scrollbar = True

    async def test_scrollbar_tracks_and_arrows_inherit_the_widget_background(self):
        widgets = [CheckboxList([(i, f'Item {i}') for i in range(30)]),
                   RadioList([(i, f'Item {i}') for i in range(30)]),
                   TextArea(text='Line\n' * 30, scrollbar=True)]
        for widget in widgets:
            widget.window.height = 5
            popup = Dialog(body=widget, size=PopupSize.CUSTOM, buttons=[Button('Close')])
            self.view.dialogs.show(popup, widget)
            screen, _, _ = self.paint(popup)
            self.assert_track_background(screen, self.view.theme.accent5[1:])
        # A page list has no popup-specific fill; an explicitly colored widget
        # must keep its own surface rather than either palette default.
        for background in ('default', '#314159'):
            widget = RadioList([(i, f'Item {i}') for i in range(30)])
            widget.window.height = 5
            screen = Screen()
            widget.window.write_to_screen(screen, MouseHandlers(), WritePosition(0, 0, 30, 5),
                                          f'class:hub bg:{background}', True, None)
            self.assert_track_background(screen, background.lstrip('#'))

    async def test_pane_scrollbar_inherits_containing_surface(self):
        pane = ScrollablePane(HSplit([Window(height=30)]))
        screen = Screen()
        pane.write_to_screen(screen, MouseHandlers(), WritePosition(0, 0, 30, 6),
                             'class:hub bg:#314159', True, None)
        self.assert_track_background(screen, '314159')

    def assert_track_background(self, screen, expected):
        tracks = [char for row in screen.data_buffer.values() for char in row.values()
                  if any(style in char.style for style in
                         (ThemeSelector.SCROLLBAR_TRACK.style, ThemeSelector.SCROLLBAR_ARROW.style))]
        self.assertTrue(tracks, 'The test must paint an actual scrollbar')
        for char in tracks:
            self.assertEqual(self.view.style.get_attrs_for_style_str(char.style).bgcolor, expected)
