"""Configured surfaces and observation-only theme selection."""

from dataclasses import asdict, replace
import unittest
from unittest.mock import patch

from prompt_toolkit.input import DummyInput
from examples.hub.preview import create_preview
from hub.config.preference_records import PreferenceRecord
from hub.config.theme import HubTheme
from hub.config.theme_mode import TerminalTheme, ThemeMode
from hub.config.theme_style import ThemeSelector
from hub.ui.output.markdown import HubSyntaxTheme
from pygments.token import Token
from tests.hub.support import SizedOutput


class ThemeSurfaceTests(unittest.TestCase):
    def test_explicit_mode_wins_and_auto_never_changes_persisted_palette(self):
        original = HubTheme(mode='auto', foreground='#123456', background='default')
        record = asdict(original)
        for hint, expected in ((None, 'dark'), ('15;0', 'dark'), ('0;15', 'light'),
                               ('0;default;7', 'light'), ('garbage', 'dark')):
            with self.subTest(hint=hint):
                environment = {} if hint is None else {'COLORFGBG': hint}
                resolved = TerminalTheme.resolve(original, environment)
                self.assertEqual(resolved.mode, expected)
                self.assertEqual(asdict(resolved) | {'mode': 'auto'}, record)
                self.assertEqual(asdict(original), record)
                explicit = replace(original, mode='light')
                self.assertIs(TerminalTheme.resolve(explicit, environment), explicit)

    def test_widget_and_global_pairs_remain_separate_in_both_modes(self):
        for mode in (ThemeMode.DARK, ThemeMode.LIGHT):
            with self.subTest(mode=mode):
                theme = HubTheme(mode=mode, foreground='#112233', background='#445566',
                                 white='#aabbcc', bright_black='#223344')
                expected = ('223344', 'aabbcc') if mode == 'light' else ('aabbcc', '223344')
                style = theme.style()
                for selector in (ThemeSelector.SETTINGS_INPUT, ThemeSelector.PTK_BUTTON,
                                 ThemeSelector.POPUP_OUTPUT, ThemeSelector.POPUP_LIST_ITEM,
                                 ThemeSelector.PTK_DIALOG_BODY_TEXT_AREA, ThemeSelector.CHECKLIST,
                                 ThemeSelector.DROPDOWN, ThemeSelector.USER_BLOCK,
                                 ThemeSelector.HEADER, ThemeSelector.FOOTER):
                    attrs = style.get_attrs_for_style_str(' '.join('class:' + part for part in selector.value.split()))
                    self.assertEqual((attrs.color, attrs.bgcolor), expected, selector)
                for selector in (ThemeSelector.COMPOSER, ThemeSelector.AUXILIARY_BUTTON,
                                 ThemeSelector.PTK_DIALOG_BODY):
                    attrs = style.get_attrs_for_style_str(' '.join('class:' + part for part in selector.value.split()))
                    self.assertEqual((attrs.color, attrs.bgcolor), ('112233', '445566'), selector)
                syntax = HubSyntaxTheme(theme)
                self.assertEqual(syntax.get_style_for_token(Token.Text).color.get_truecolor().hex,
                                 theme.widget_foreground)
                self.assertEqual(syntax.get_background_style().bgcolor.get_truecolor().hex,
                                 theme.widget_background)

    def test_auto_view_uses_one_resolved_copy_without_overwriting_settings(self):
        with patch.dict('os.environ', {'COLORFGBG': '0;15'}):
            app, view, _ = create_preview(input=DummyInput(), output=SizedOutput(),
                                          theme=HubTheme(mode='auto'))
        self.addCleanup(view.close)
        self.assertEqual(view.theme.mode, 'auto')
        self.assertEqual(view.render_theme.mode, 'light')
        self.assertEqual(view.output.transcript.control.theme, view.render_theme)
        with patch.dict('os.environ', {'COLORFGBG': '0;15'}), patch.object(view.output, 'set_theme') as apply:
            view.set_theme(replace(view.theme))
            apply.assert_not_called()
            view.set_theme(replace(view.theme, mode='dark'))
            apply.assert_called_once_with(view.render_theme)
        self.assertEqual(view.theme.mode, 'dark')

    def test_current_record_validation_does_not_migrate_missing_or_old_fields(self):
        record = asdict(HubTheme())
        self.assertEqual(PreferenceRecord.validate_section(HubTheme, 'appearance', record), record)
        record.pop('mode')
        with self.assertRaises(TypeError):
            PreferenceRecord.validate_section(HubTheme, 'appearance', record)
        with self.assertRaises(ValueError):
            HubTheme(mode='white')
