"""Title, progress, button selection and shortcut spacing owner contracts."""
from dok import DockConfig, ShortcutsConfig
from pathlib import Path
import tempfile
import unittest
import jsonschema
from dok import ConfigStore, PlaybackAction, ProgressConfig, SettingsError, Shortcut, TitleConfig, TransportButton, TransportConfig

class DisplaySettingsTests(unittest.TestCase):

    def test_initial_button_colors_and_keys_and_cross_widget_conflicts(self):
        from dok.core.styles import MutedStyle
        buttons = DockConfig.initial().widgets.media_controls.buttons
        self.assertEqual([b.key.to_list() for b in buttons], [["f5"], ["f6"], ["f7"]])
        self.assertEqual([b.style.color for b in buttons], ["", "", ""])
        self.assertTrue(all(b.style.foreground == "fg:default" for b in buttons))
        self.assertEqual(TransportButton("play", style=MutedStyle("#112233")).style.foreground, "#112233")
        self.assertEqual(TransportButton("play", style={"color": "#ABCDEF"}).style.foreground, "#abcdef")
        for key in (("c-w",), ("escape", "1")):
            with self.subTest(key=key), self.assertRaises(SettingsError):
                DockConfig(widgets={"media_controls": {"buttons": [{"action": "play", "key": key}]},
                                    "shortcuts": {"items": [Shortcut(1, "ls", key=("escape", "1", "x"))]}})

    def test_display_settings_roundtrip_and_defaults(self):
        config = DockConfig(widgets={
            'title': TitleConfig(
                texts={'prefix': 'Now: ', 'audio_error': '오디오 오프라인'},
                style={'color': '#AABBCC'}
            ),
            'progress': ProgressConfig(width_columns=30, style={'color': '#123456'}),
            'media_controls': TransportConfig(buttons=(
                TransportButton('stop', text='STOP'),
                TransportButton('play', style={'color': '#abcdef'})
            )),
            'shortcuts': ShortcutsConfig(spacing={'item_gap_columns': 3})
        })
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / 'config.jsonc')
            store.save(config)
            self.assertEqual(store.load(), config)
        defaults = DockConfig.from_dict({})
        self.assertEqual(defaults.widgets.title.application_caption(('Player',)), 'Now playing: Player')
        self.assertEqual(defaults.widgets.title.audio_error_caption('backend error'), 'Audio connection failed')
        self.assertEqual(defaults.widgets.progress.width_columns, 48)
        self.assertEqual(
            [button.display() for button in defaults.widgets.media_controls.buttons],
            ['\U000f04ae', '\U000f040e', '\U000f04ad']
        )
        self.assertEqual(defaults.widgets.shortcuts.spacing.item_gap_columns, 2)

    def test_prefix_can_be_empty_without_changing_application_names(self):
        self.assertEqual(
            TitleConfig(texts={'prefix': ''}).application_caption(('Firefox', 'VLC')),
            'Firefox · VLC'
        )
        self.assertEqual(TitleConfig(texts={'prefix': 'NOW '}).application_caption(()), '')
        self.assertEqual(TitleConfig(texts={'prefix': 'NOW '}).application_caption(('VLC',)), 'NOW VLC')

    def test_audio_error_caption_can_be_customized_or_hidden(self):
        config = TitleConfig(texts={'audio_error': '오디오 오프라인'})
        self.assertEqual(config.audio_error_caption('server unavailable'), '오디오 오프라인')
        self.assertEqual(config.audio_error_caption(''), '')
        self.assertEqual(TitleConfig(texts={'audio_error': ''}).audio_error_caption('failed'), '')

    def test_invalid_display_settings_are_rejected_by_runtime_and_schema(self):
        cases = [
            {'widgets': {'title': {'style': {'color': 'red'}}}},
            {'widgets': {'title': {'texts': {'prefix': 'bad\ntext'}}}},
            {'widgets': {'title': {'texts': {'audio_error': 'bad\ntext'}}}},
            {'widgets': {'title': {'texts': {'audio_error': 1}}}},
            {'widgets': {'progress': {'width_columns': 0}}},
            {'widgets': {'progress': {'width_columns': 1.5}}},
            {'widgets': {'progress': {'style': {'color': '#fff'}}}},
            {'widgets': {'shortcuts': {'spacing': {'item_gap_columns': -1}}}},
            {'widgets': {'shortcuts': {'spacing': {'item_gap_columns': True}}}},
            {'widgets': {'media_controls': {'buttons': [{'action': 'unknown'}]}}},
            {'widgets': {'media_controls': {'buttons': [{'text': 'PLAY'}]}}},
            {'widgets': {'media_controls': {'buttons': [{'action': 'play', 'text': 'bad\ntext'}]}}},
            {'widgets': {'media_controls': {'buttons': [{'action': 'play', 'style': {'color': 'red'}}]}}},
            {'widgets': {'media_controls': {'buttons': [{'action': 'play'}, {'action': 'play', 'text': 'GO'}]}}}
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
        self.assertEqual(ProgressConfig(width_columns=10 ** 100).display_length(20), 20)
        self.assertEqual(ProgressConfig().display_length(-1), 0)
        self.assertEqual(len(ShortcutsConfig(spacing={'item_gap_columns': 10 ** 100}).separator(80)), 5)
        self.assertEqual(ShortcutsConfig(spacing={'item_gap_columns': 3}).separator(39), ' ')
        self.assertEqual(ShortcutsConfig(spacing={'item_gap_columns': 0}).separator(80), '')
        self.assertEqual(Shortcut(1, 'git').compose('status'), 'git status')
