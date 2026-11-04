"""Owner codecs, gradient interpolation and persisted appearance settings."""
from dok import DockConfig
from pathlib import Path
import tempfile
import unittest
import jsonschema
from dok import ConfigStore, Separator, SettingsError, Shortcut, VisualizerConfig
from dok.core.colors import DockPalette

class AppearanceTests(unittest.TestCase):

    def test_schema_and_runtime_reject_the_same_invalid_colors(self):
        for color in ('red', '#fff', '#000000\n', '#gg0000', 'bg:red', '', None, 12):
            for data in (
                {'widgets': {'shortcuts': {'items': [{
                    'slot': 1,
                    'command': 'git',
                    'style': {'color': color},
                    'type': 'shortcut'
                }]}}},
                {'widgets': {'visualizer': {'style': {'colors': [color]}}}}
            ):
                with self.subTest(data=data):
                    with self.assertRaises(SettingsError):
                        DockConfig.from_dict(data)
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.validate(data, DockConfig.schema())
        for data in (
            {'widgets': {'visualizer': {'style': {'colors': []}}}},
            {'widgets': {'visualizer': {'style': {'colors': '#123456'}}}}
        ):
            with self.assertRaises(SettingsError):
                DockConfig.from_dict(data)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(data, DockConfig.schema())

    def test_gradient_endpoints_midpoints_and_single_cell(self):
        config = VisualizerConfig(style={'colors': ('#FF0000', '#00FF00', '#0000FF')})
        self.assertEqual(config.style.palette(5), ('#ff0000', '#808000', '#00ff00', '#008080', '#0000ff'))
        self.assertEqual(config.style.palette(1), ('#ff0000',))
        self.assertEqual(config.style.palette(0), ())
        self.assertEqual(VisualizerConfig(style={'colors': ('#123456',)}).style.palette(4), ('#123456',) * 4)
        for cells in (-1, 1.5, True):
            with self.assertRaises(SettingsError):
                config.style.palette(cells)

    def test_colors_roundtrip_through_owner_store_and_schema(self):
        config = DockConfig(widgets={
            'shortcuts': {'items': (Shortcut(1, 'git', style={'color': '#AaBBCC'}),)},
            'visualizer': VisualizerConfig(style={'colors': ('#abcdef', '#FEDCBA')}, width_columns=48)
        })
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / 'config.jsonc')
            store.save(config)
            self.assertEqual(store.load(), config)
        self.assertEqual(config.widgets.shortcuts.shortcut(1).style.color, '#aabbcc')
        self.assertEqual(
            DockConfig().widgets.visualizer.style.colors,
            (DockPalette.ACCENT, '#c084fc', '#fb7185')
        )
        self.assertEqual(Shortcut(1, 'git').style.color, DockPalette.ACCENT)

    def test_length_validation_and_default_are_shared_with_schema(self):
        config = VisualizerConfig()
        self.assertEqual(config.width_columns, 38)
        self.assertEqual(
            VisualizerConfig.schema()['properties'][VisualizerConfig.names().width_columns]['default'],
            config.width_columns
        )
        for length in (0, -1, 2.5, True, '48', None):
            data = {'widgets': {'visualizer': {'width_columns': length}}}
            with self.subTest(length=length):
                with self.assertRaises(SettingsError):
                    DockConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, DockConfig.schema())

    def test_display_length_preserves_range_and_clamps_before_allocating(self):
        levels = (0.0, 0.8, 0.2, 1.0)
        self.assertEqual(VisualizerConfig(width_columns=2).display_levels(levels, 80), (0.8, 1.0))
        self.assertEqual(VisualizerConfig(width_columns=1).display_levels(levels, 80), (1.0,))
        self.assertEqual(
            VisualizerConfig(width_columns=5).display_levels((0.0, 1.0), 80),
            (0.0, 0.25, 0.5, 0.75, 1.0)
        )
        self.assertEqual(VisualizerConfig(width_columns=10 ** 100).display_levels(levels, 2), (0.8, 1.0))
        self.assertEqual(VisualizerConfig(width_columns=10).display_levels(levels, 0), ())
        self.assertEqual(VisualizerConfig(width_columns=4).display_levels(levels, 80), levels)

    def test_registered_shortcuts_never_use_numeric_placeholders(self):
        self.assertEqual(Shortcut(1, 'git', icon='🌿', label='Git').display(), '🌿 Git')
        self.assertEqual(Shortcut(1, 'git', icon='🌿').display(), '🌿')
        self.assertEqual(Shortcut(1, 'git', label='Git').display(), 'Git')
        self.assertEqual(Shortcut(1, 'git').display(), 'git')
        self.assertEqual(Shortcut(1, 'git', icon='🌿', label='Git').display(detailed=False), '🌿')

    def test_separator_roundtrip_schema_and_action_lookup(self):
        config = DockConfig(widgets={'shortcuts': {'items': (
            Shortcut(1, 'git'),
            Separator(2, text='┃', style={'color': '#AABBCC'})
        )}})
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / 'config.jsonc')
            store.save(config)
            self.assertEqual(store.load(), config)
        self.assertEqual(
            config.widgets.shortcuts.entry(2),
            Separator(2, text='┃', style={'color': '#aabbcc'})
        )
        self.assertIsNone(config.widgets.shortcuts.shortcut(2))
        self.assertEqual(config.widgets.shortcuts.shortcut(1).command, 'git')
        self.assertFalse(config.widgets.shortcuts.entry(2).enabled)
        self.assertIsNone(config.widgets.shortcuts.entry(2).key)
        self.assertEqual(Separator(3).display(), '│')
        with self.assertRaises(SettingsError):
            DockConfig(widgets={'shortcuts': {'items': (Shortcut(1, 'git'), Separator(1))}})

    def test_separator_discriminator_and_fields_are_validated_by_owner(self):
        for entry in (
            {'slot': 1},
            {'slot': 1, 'type': 'invalid'},
            {'slot': 1, 'type': 'separator', 'command': 'git'},
            {'slot': 1, 'type': 'separator', 'key': ['c-g']},
            {'slot': 1, 'type': 'separator', 'text': ''},
            {'slot': 1, 'type': 'separator', 'text': '\n'},
            {'slot': 1, 'type': 'separator', 'style': {'color': 'red'}}
        ):
            data = {'widgets': {'shortcuts': {'items': [entry]}}}
            with self.subTest(entry=entry):
                with self.assertRaises(SettingsError):
                    DockConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, DockConfig.schema())
