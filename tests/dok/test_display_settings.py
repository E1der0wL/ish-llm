"""Title, progress, button selection and shortcut spacing owner contracts."""

from pathlib import Path
import tempfile
import unittest

import jsonschema

from dok import (ConfigStore, DockConfig, PlaybackAction, ProgressConfig, SettingsError,
                 Shortcut, ShortcutLayout, TitleConfig, TransportButton, TransportConfig)


class DisplaySettingsTests(unittest.TestCase):
    def test_display_settings_roundtrip_and_legacy_defaults(self):
        config = DockConfig(title=TitleConfig(prefix="Now: ", color="#AABBCC"),
                            progress=ProgressConfig(length=30, color="#123456"),
                            transport=TransportConfig(buttons=(TransportButton("stop", text="STOP"),
                                                               TransportButton("play", color="#abcdef"))),
                            shortcut_layout=ShortcutLayout(padding=3))
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "config.json")
            store.save(config)
            self.assertEqual(store.load(), config)
        defaults = DockConfig.from_dict({})
        self.assertEqual(defaults.title.application_caption(("Player",)), "재생 중: Player")
        self.assertEqual(defaults.progress.length, 48)
        self.assertEqual([button.display() for button in defaults.transport.buttons], ["⏮", "⏯", "⏭"])
        self.assertEqual(defaults.shortcut_layout.padding, 1)

    def test_prefix_can_be_empty_without_changing_application_names(self):
        self.assertEqual(TitleConfig(prefix="").application_caption(("Firefox", "VLC")), "Firefox · VLC")
        self.assertEqual(TitleConfig(prefix="NOW ").application_caption(()), "")
        self.assertEqual(TitleConfig(prefix="NOW ").application_caption(("VLC",)), "NOW VLC")

    def test_invalid_display_settings_are_rejected_by_runtime_and_schema(self):
        cases = [
            {"title": {"color": "red"}}, {"title": {"prefix": "bad\ntext"}},
            {"progress": {"length": 0}}, {"progress": {"length": 1.5}},
            {"progress": {"color": "#fff"}},
            {"shortcut_layout": {"padding": -1}}, {"shortcut_layout": {"padding": True}},
            {"transport": {"buttons": [{"action": "unknown"}]}},
            {"transport": {"buttons": [{"text": "PLAY"}]}},
            {"transport": {"buttons": [{"action": "play", "text": "bad\ntext"}]}},
            {"transport": {"buttons": [{"action": "play", "color": "red"}]}},
            {"transport": {"buttons": [{"action": "play"}, {"action": "play", "text": "GO"}]}},
        ]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(SettingsError):
                    DockConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, DockConfig.schema())

    def test_button_selection_and_symbols_are_owned_by_actions(self):
        config = TransportConfig(buttons=tuple(TransportButton(action) for action in PlaybackAction))
        self.assertEqual([button.display() for button in config.buttons], [action.symbol for action in PlaybackAction])
        self.assertEqual(TransportButton("play", text="GO").display(), "GO")
        self.assertEqual(TransportConfig(buttons=()).buttons, ())
        jsonschema.validate(TransportConfig(buttons=()).to_dict(), TransportConfig.schema())

    def test_huge_lengths_and_padding_are_bounded_by_available_width(self):
        self.assertEqual(ProgressConfig(length=10**100).display_length(20), 20)
        self.assertEqual(ProgressConfig().display_length(-1), 0)
        self.assertEqual(len(ShortcutLayout(padding=10**100).separator(80)), 5)
        self.assertEqual(ShortcutLayout(padding=3).separator(39), " ")
        self.assertEqual(ShortcutLayout(padding=0).separator(80), "")
        self.assertEqual(Shortcut(1, "git").compose("status"), "git status")
