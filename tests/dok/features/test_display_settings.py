"""Title, progress, button selection and shortcut spacing owner contracts."""
from dok import DockConfig, ShortcutsConfig, Locale
from pathlib import Path
import tempfile
import unittest
import jsonschema
from dok import ConfigStore, PlaybackAction, ProgressConfig, SettingsError, Shortcut, TitleConfig, TransportButton, TransportConfig

class DisplaySettingsTests(unittest.TestCase):

    def test_terminal_text_defaults_and_theme_reference_roundtrip(self):
        from dok.core.styles import TextStyle
        from dok.settings.jsonc import Jsonc
        for config in (DockConfig(), DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(DockConfig.initial())))):
            widgets, toolbar = config.widgets, config.toolbar
            styles = (widgets.weekday.style, widgets.date.style, widgets.time.style,
                      widgets.weather.style, widgets.title.style, toolbar.date.style, toolbar.numbers.style,
                      *(item.style for item in toolbar.input.items))
            self.assertTrue(all(style.color == "foreground" and style.foreground == "fg:default" for style in styles))
            self.assertEqual(widgets.weather.style.low_color, "low_temp")
            self.assertTrue(widgets.date.style.bold)
        for raw, expected in (({}, "foreground"), ({"color": "foreground"}, "foreground"),
                              ({"color": "bright_white"}, "bright_white")):
            jsonschema.validate(raw, TextStyle.schema())
            self.assertEqual(TextStyle.from_dict(raw).to_dict(), {"color": expected})

    def test_initial_button_colors_and_keys_and_cross_widget_conflicts(self):
        from dok.core.styles import TextStyle
        buttons = DockConfig.initial().widgets.controls.buttons
        self.assertEqual([b.key.to_list() for b in buttons], [["f5"], ["f6"], ["f7"]])
        self.assertEqual([b.style.color for b in buttons], ["foreground", "foreground", "foreground"])
        self.assertTrue(all(b.style.foreground == "fg:default" for b in buttons))
        self.assertEqual(TransportButton("play", style=TextStyle("cyan")).style.foreground, "#67e8f9")
        self.assertEqual(TransportButton("play", style={"color": "red"}).style.foreground, "#e58c76")
        for key in (("c-w",), ("escape", "1")):
            with self.subTest(key=key), self.assertRaises(SettingsError):
                DockConfig(widgets={"controls": {"buttons": [{"action": "play", "key": key}]},
                                    "shortcuts": {"items": [Shortcut(1, "ls", key=("escape", "1", "x"))]}})

    def test_display_settings_roundtrip_and_defaults(self):
        config = DockConfig(widgets={
            'title': TitleConfig(
                style={'color': 'cyan'}
            ),
            'progress': ProgressConfig(width=30, style={'color': 'muted'}),
            'controls': TransportConfig(buttons=(
                TransportButton('stop', text='STOP'),
                TransportButton('play', style={'color': 'green'})
            )),
            'shortcuts': ShortcutsConfig(spacing={'gap': 3})
        })
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / 'config.jsonc')
            store.save(config)
            self.assertEqual(store.load(), config)
        defaults = DockConfig.from_dict({})
        self.assertEqual(defaults.widgets.title.application_caption(('Player',)), 'Now playing: Player')
        self.assertEqual(defaults.widgets.title.audio_error_caption('backend error'), 'Audio connection failed')
        self.assertEqual(defaults.widgets.progress.width, 48)
        self.assertEqual(
            [button.display() for button in defaults.widgets.controls.buttons],
            ['\U000f04ae', '\U000f040e', '\U000f04ad']
        )
        self.assertEqual(defaults.widgets.shortcuts.spacing.gap, 2)

    def test_prefix_uses_language_pack_without_changing_application_names(self):
        self.assertEqual(
            TitleConfig().application_caption(('Firefox', 'VLC'), Locale.resolve('ko')),
            '재생 중 : Firefox · VLC'
        )
        self.assertEqual(TitleConfig().application_caption(()), '')
        self.assertEqual(TitleConfig().application_caption(('VLC',)), 'Now playing: VLC')

    def test_audio_error_caption_uses_language_pack_and_keeps_diagnostics_separate(self):
        config = TitleConfig()
        self.assertEqual(config.audio_error_caption('server unavailable'), 'Audio connection failed')
        self.assertEqual(config.audio_error_caption(''), '')
        self.assertEqual(config.audio_error_caption('failed', Locale.resolve('ko')), '사운드 연결 실패')

    def test_invalid_display_settings_are_rejected_by_runtime_and_schema(self):
        cases = [
            {'widgets': {'title': {'style': {'color': 'invalid-role'}}}},
            {'widgets': {'title': {'texts': {'prefix': 'bad\ntext'}}}},
            {'widgets': {'title': {'texts': {'audio_error': 'bad\ntext'}}}},
            {'widgets': {'title': {'texts': {'audio_error': 1}}}},
            {'widgets': {'progress': {'width': 0}}},
            {'widgets': {'progress': {'width': 1.5}}},
            {'widgets': {'progress': {'style': {'color': '#fff'}}}},
            {'widgets': {'shortcuts': {'spacing': {'gap': -1}}}},
            {'widgets': {'shortcuts': {'spacing': {'gap': True}}}},
            {'widgets': {'controls': {'buttons': [{'action': 'unknown'}]}}},
            {'widgets': {'controls': {'buttons': [{'text': 'PLAY'}]}}},
            {'widgets': {'controls': {'buttons': [{'action': 'play', 'text': 'bad\ntext'}]}}},
            {'widgets': {'controls': {'buttons': [{'action': 'play', 'style': {'color': 'invalid-role'}}]}}},
            {'widgets': {'controls': {'buttons': [{'action': 'play'}, {'action': 'play', 'text': 'GO'}]}}}
        ]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(SettingsError):
                    DockConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, DockConfig.schema())

    def test_button_selection_and_symbols_are_owned_by_actions(self):
        config = TransportConfig(buttons=tuple((TransportButton(action) for action in PlaybackAction)))
        self.assertEqual(
            [button.display() for button in config.buttons],
            [action.symbol for action in PlaybackAction]
        )
        self.assertEqual(TransportButton('play', text='GO').display(), 'GO')
        self.assertEqual(TransportConfig(buttons=()).buttons, ())
        jsonschema.validate(TransportConfig(buttons=()).to_dict(), TransportConfig.schema())

    def test_huge_lengths_and_padding_are_bounded_by_available_width(self):
        self.assertEqual(ProgressConfig(width=10 ** 100).display_length(20), 20)
        self.assertEqual(ProgressConfig().display_length(-1), 0)
        self.assertEqual(len(ShortcutsConfig(spacing={'gap': 10 ** 100}).separator(80)), 5)
        self.assertEqual(ShortcutsConfig(spacing={'gap': 3}).separator(39), ' ')
        self.assertEqual(ShortcutsConfig(spacing={'gap': 0}).separator(80), '')
        self.assertEqual(Shortcut(1, 'git').compose('status'), 'git status')
