"""Shared theme behavior across renderers; run after UI approval."""
from dataclasses import replace
from datetime import datetime
import unittest

import jsonschema
from prompt_toolkit.styles import Style

from dok import DockConfig, Theme, ThemeColors, ThemeRoles, ThemeRole, SettingsError
from dok.core.theme import ThemeReference, ThemeBackground
from dok.dashboard.view import DockView
from dok.features.audio.models import AudioSnapshot
from dok.features.media.models import PlaybackSnapshot
from dok.features.system.models import SystemSnapshot
from dok.features.toolbar.editor import EditorSnapshot
from dok.features.toolbar.view import ToolbarView
from dok.popups.config import PopupRect
from dok.popups.models import PopupContent
from dok.rendering.popups import PopupSurface
from dok.settings.jsonc import Jsonc


class ThemeTests(unittest.TestCase):
    def test_declarations_drive_serialization_schema_and_generated_comments(self):
        config = DockConfig.initial()
        text = Jsonc.dumps(config)
        self.assertEqual(DockConfig.from_dict(Jsonc.loads(text)), config)
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        self.assertEqual(len(config.theme.colors.to_dict()), 18)
        self.assertIn('"bright_comment"', text)
        self.assertIn('null means terminal default', text)
        for reference in ThemeReference.choices():
            jsonschema.validate(reference, ThemeReference().schema())
            config.theme.resolve(reference)
        for theme in ({"colors": {"red": "bad"}}, {"roles": {"title": "foreground"}},
                      {"roles": {"title": "#123456"}}, {"accent_1": "#123456"}):
            with self.subTest(theme=theme):
                with self.assertRaises(SettingsError):
                    Theme.from_dict(theme)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(theme, Theme.schema())

    def test_terminal_defaults_and_bright_colors_are_independent_of_bold(self):
        theme = Theme()
        attrs = Style([]).get_attrs_for_style_str(theme.paint())
        self.assertEqual((attrs.color, attrs.bgcolor), ("default", "default"))
        attrs = Style([]).get_attrs_for_style_str(theme.paint("bright_red"))
        self.assertEqual(attrs.color, theme.colors.bright_red[1:])
        self.assertFalse(attrs.bold)
        with self.assertRaises(SettingsError):
            DockConfig(widgets={"time": {"style": {"color": "#123456"}}})

    def test_toolbar_background_rgb_override_keeps_shared_palette_and_roles(self):
        initial, defaults = DockConfig.initial(), DockConfig()
        self.assertEqual(initial.theme, defaults.theme)
        self.assertIsNone(defaults.theme.resolve_background(defaults.toolbar.background))
        self.assertEqual(initial.theme.resolve_background(initial.toolbar.background), '#1d2035')
        for value in ('background', 'black', '#1D2035'):
            jsonschema.validate(value, ThemeBackground().schema())
            normalized = ThemeBackground().decode(value)
            self.assertEqual(Theme().resolve_background(normalized), Theme().resolve_background(value))
        for value in ('#123', '#gg2035', 'invalid', None):
            with self.subTest(value=value):
                with self.assertRaises(SettingsError):
                    ThemeBackground().decode(value)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(value, ThemeBackground().schema())
        with self.assertRaises(SettingsError):
            DockConfig(toolbar={'regions': {'cpu': {'style': {'background': '#1D2035'}}}})

    def test_one_palette_change_updates_toolbar_and_popup_without_changing_exceptions(self):
        config = DockConfig.initial()
        changed = replace(config, theme=Theme(colors={"cyan": "#123456", "green": "#abcdef"}))
        self.assertEqual(config.widgets.shortcuts, changed.widgets.shortcuts)
        self.assertEqual(config.widgets.visualizer, changed.widgets.visualizer)
        toolbar = ToolbarView().render(changed.toolbar, EditorSnapshot(), SystemSnapshot(cpu_percent=12), (),
                                      datetime(2026, 11, 7), True, 160, theme=changed.theme)
        self.assertTrue(any("#abcdef" in style for style, text in toolbar if "12%" in text))
        surface = PopupSurface(lambda: None, lambda: None, modal=True)
        content = PopupContent.plain("Header", "Body")
        rect = PopupRect(0, 0, 40, 10)
        surface.draw(content, config.popups.general, rect, theme=config.theme)
        surface.draw(content, config.popups.general, rect, theme=changed.theme)
        self.assertTrue(any("#123456" in part[0] for part in surface.fragments if "Header" in part[1]))
        self.assertTrue(any("" in part[1] for part in surface.fragments))

    def test_background_covers_dock_padding_and_direct_colors_survive_foreground_change(self):
        config = replace(DockConfig.initial(), theme=Theme(roles={"foreground": "red", "background": "black"}))
        lines = DockView(lambda slot: None).render(config, PlaybackSnapshot(), AudioSnapshot(), "", 120, 80)
        self.assertTrue(all(line.left == 20 and line.width == 80 for line in lines))
        self.assertTrue(all("bg:#253047" in part[0] for line in lines for part in line.fragments))
        fragments = [part for line in lines for part in line.fragments]
        self.assertTrue(any("#e67e80" in style for style, text, *_ in fragments if "Find" in text))
        self.assertTrue(any("#67e8f9" in part[0] for part in fragments))
