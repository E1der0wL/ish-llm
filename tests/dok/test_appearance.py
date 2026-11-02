"""Owner codecs, gradient interpolation and persisted appearance settings."""

from pathlib import Path
import tempfile
import unittest

import jsonschema

from dok import ConfigStore, DockConfig, Separator, SettingsError, Shortcut, VisualizerConfig
from dok.appearance import DockPalette


class AppearanceTests(unittest.TestCase):
    def test_schema_and_runtime_reject_the_same_invalid_colors(self):
        for color in ("red", "#fff", "#000000\n", "#gg0000", "bg:red", "", None, 12):
            for data in ({"shortcuts": [{"slot": 1, "command": "git", "color": color}]},
                         {"visualizer": {"colors": [color]}}):
                with self.subTest(data=data):
                    with self.assertRaises(SettingsError):
                        DockConfig.from_dict(data)
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.validate(data, DockConfig.schema())
        for data in ({"visualizer": {"colors": []}}, {"visualizer": {"colors": "#123456"}}):
            with self.assertRaises(SettingsError):
                DockConfig.from_dict(data)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(data, DockConfig.schema())

    def test_gradient_endpoints_midpoints_and_single_cell(self):
        config = VisualizerConfig(colors=("#FF0000", "#00FF00", "#0000FF"))
        self.assertEqual(config.palette(5), ("#ff0000", "#808000", "#00ff00", "#008080", "#0000ff"))
        self.assertEqual(config.palette(1), ("#ff0000",))
        self.assertEqual(config.palette(0), ())
        self.assertEqual(VisualizerConfig(colors=("#123456",)).palette(4), ("#123456",) * 4)
        for cells in (-1, 1.5, True):
            with self.assertRaises(SettingsError):
                config.palette(cells)

    def test_colors_roundtrip_through_owner_store_and_schema(self):
        config = DockConfig(shortcuts=(Shortcut(1, "git", color="#AaBBCC"),),
                            visualizer=VisualizerConfig(colors=("#abcdef", "#FEDCBA"), length=48))
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "config.json")
            store.save(config)
            self.assertEqual(store.load(), config)
        self.assertEqual(config.shortcut(1).color, "#aabbcc")
        self.assertEqual(DockConfig().visualizer.colors, (DockPalette.ACCENT,))
        self.assertEqual(Shortcut(1, "git").color, DockPalette.ACCENT)

    def test_length_validation_and_default_are_shared_with_schema(self):
        config = VisualizerConfig()
        self.assertEqual(config.length, 24)
        self.assertEqual(VisualizerConfig.schema()["properties"][VisualizerConfig.names().length]["default"],
                         config.length)
        for length in (0, -1, 2.5, True, "48", None):
            data = {"visualizer": {"length": length}}
            with self.subTest(length=length):
                with self.assertRaises(SettingsError):
                    DockConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, DockConfig.schema())

    def test_display_length_preserves_range_and_clamps_before_allocating(self):
        levels = (0.0, 0.8, 0.2, 1.0)
        self.assertEqual(VisualizerConfig(length=2).display_levels(levels, 80), (0.8, 1.0))
        self.assertEqual(VisualizerConfig(length=1).display_levels(levels, 80), (1.0,))
        self.assertEqual(VisualizerConfig(length=5).display_levels((0.0, 1.0), 80),
                         (0.0, 0.25, 0.5, 0.75, 1.0))
        self.assertEqual(VisualizerConfig(length=10**100).display_levels(levels, 2), (0.8, 1.0))
        self.assertEqual(VisualizerConfig(length=10).display_levels(levels, 0), ())
        self.assertEqual(VisualizerConfig(length=4).display_levels(levels, 80), levels)

    def test_registered_shortcuts_never_use_numeric_placeholders(self):
        self.assertEqual(Shortcut(1, "git", emoji="🌿", label="Git").display(), "🌿 Git")
        self.assertEqual(Shortcut(1, "git", emoji="🌿").display(), "🌿")
        self.assertEqual(Shortcut(1, "git", label="Git").display(), "Git")
        self.assertEqual(Shortcut(1, "git").display(), "git")
        self.assertEqual(Shortcut(1, "git", emoji="🌿", label="Git").display(detailed=False), "🌿")

    def test_separator_roundtrip_schema_and_action_lookup(self):
        config = DockConfig(shortcuts=(Shortcut(1, "git"), Separator(2, text="┃", color="#AABBCC")))
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "config.json")
            store.save(config)
            self.assertEqual(store.load(), config)
        self.assertEqual(config.entry(2), Separator(2, text="┃", color="#aabbcc"))
        self.assertIsNone(config.shortcut(2))
        self.assertEqual(config.shortcut(1).command, "git")
        self.assertFalse(config.entry(2).enabled)
        self.assertIsNone(config.entry(2).key)
        self.assertEqual(Separator(3).display(), "│")
        with self.assertRaises(SettingsError):
            DockConfig(shortcuts=(Shortcut(1, "git"), Separator(1)))

    def test_separator_discriminator_and_fields_are_validated_by_owner(self):
        for entry in ({"slot": 1}, {"slot": 1, "kind": "invalid"},
                      {"slot": 1, "kind": "separator", "command": "git"},
                      {"slot": 1, "kind": "separator", "key": ["c-g"]},
                      {"slot": 1, "kind": "separator", "text": ""},
                      {"slot": 1, "kind": "separator", "text": "\n"},
                      {"slot": 1, "kind": "separator", "color": "red"}):
            data = {"shortcuts": [entry]}
            with self.subTest(entry=entry):
                with self.assertRaises(SettingsError):
                    DockConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, DockConfig.schema())
