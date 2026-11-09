"""Current key labels, conditional notification help and bounded toolbar hints."""
from dataclasses import replace
from datetime import datetime
import unittest

from dok import DockConfig, Locale, PopupContent
from dok.integrations.ish.keys import KeySequence
from dok.popups.config import NotificationConfig, PopupRect
from dok.rendering.popups import PopupSurface
from dok.rendering.text import width
from dok.features.toolbar.view import ToolbarView
from dok.features.toolbar.editor import EditorSnapshot
from dok.features.system.models import SystemSnapshot


class KeyHintTests(unittest.TestCase):
    def test_modified_letters_are_not_misread_as_modifiers(self):
        for sequence, label in ((('escape', 'c'), 'Alt+C'), (('escape', 'm'), 'Alt+M'),
                                (('escape', 's'), 'Alt+S'), (('c-c',), 'Ctrl+C'),
                                (('escape', 'c-x'), 'Alt+Ctrl+X'), (('f2',), 'F2')):
            self.assertEqual(KeySequence(sequence).label(), label)

    def test_notification_footer_follows_available_actions_keys_and_language(self):
        config = NotificationConfig()
        self.assertEqual(config.status_text(locale=Locale.resolve('ko')), 'Alt+X 닫기')
        self.assertEqual(config.status_text(details=True), 'Alt+N Details · Alt+X Close')
        changed = replace(config, dismiss_key=('f8',), details_key=None)
        self.assertEqual(changed.status_text(details=True), 'F8 Close')
        self.assertEqual(replace(changed, dismiss_key=None).status_text(), '')
        surface = PopupSurface(lambda: None, lambda: None, modal=False)
        surface.draw(PopupContent.plain('Title', 'Body'), config.panel, PopupRect(0, 0, 40, 10),
                     notification=True, status_hint=config.status_text(details=True))
        rows = [''.join(item[1] for item in row) for row in surface._painted_rows]
        self.assertIn('Alt+N Details · Alt+X Close', rows[-2])
        self.assertEqual(rows[-3].strip('│ '), '')
        self.assertEqual(rows[-4].strip('│ '), 'Body')
        self.assertEqual(surface._actions, ())

    def test_all_notification_heights_follow_content_and_configured_bottom_padding(self):
        config = NotificationConfig()
        surface = PopupSurface(lambda: None, lambda: None, modal=False)
        for count in (1, 3):
            content = PopupContent.plain('Reminder', '\n'.join(f'Line {i}' for i in range(count)))
            for bottom in (0, 1, 3):
                with self.subTest(count=count, bottom=bottom):
                    panel = replace(config.panel, padding=replace(config.panel.padding, bottom=bottom))
                    surface.draw(content, panel, PopupRect(0, 0, 40, 20), notification=True,
                                 status_hint=config.status_text())
                    rows = [''.join(item[1] for item in row).strip('│ ') for row in surface._painted_rows]
                    self.assertIn('Alt+X', rows[-2])
                    self.assertEqual(rows[-2-bottom:-2], [''] * bottom)
                    self.assertEqual(rows[-3-bottom], f'Line {count - 1}')
                    self.assertEqual(surface.painted_height, 5 + count + bottom)

    def test_center_hints_preserve_left_and_right_regions_and_can_be_disabled(self):
        config = DockConfig.initial()
        snapshot = SystemSnapshot(cpu_percent=25, memory_used=4, memory_total=16)
        view = ToolbarView()
        hints = ('Ctrl+Q Hub', 'Ctrl+W Dock', 'Alt+C Calendar', 'Alt+W Weather', 'Alt+O Register', 'Alt+M Manage')
        def line(columns, hints=(), settings=replace(config.toolbar, hints=True)):
            return ''.join(item[1] for item in view.render(settings, EditorSnapshot(), snapshot, (),
                datetime(2026, 11, 8), False, columns, hints=hints))
        wide = line(240, hints)
        self.assertIn(' · '.join(hints), wide)
        self.assertEqual(line(240, hints, replace(config.toolbar, hints=False)), line(240))
        for columns in (1, 40, 100, 240):
            base, with_hints = line(columns), line(columns, hints)
            self.assertEqual(width(with_hints), columns)
            self.assertEqual(with_hints[-min(columns, 19):], base[-min(columns, 19):])
        self.assertFalse(config.toolbar.hints)
        self.assertEqual(config.toolbar.hint_key.label(), 'F1')
        self.assertEqual(line(100, hints), line(100))  # No partial help or ellipsis.
        with self.assertRaises(ValueError):
            DockConfig(toolbar={'hint_key': ['escape', 'm']})
