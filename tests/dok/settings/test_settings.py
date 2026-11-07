"""Validate ownership contracts, persisted settings and shortcut interpretation."""
from dok import DockConfig
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import jsonschema
from dok import ConfigStore, KeySequence, SettingsError, Shortcut, Width, WidthMode

class SettingsTests(unittest.TestCase):

    def test_roundtrip_uses_typed_owners_and_preserves_user_text(self):
        config = DockConfig(
            dock={'width': Width(mode=WidthMode.RATIO, value=0.6)},
            widgets={'shortcuts': {'items': (
                Shortcut(slot=1, command='rg -n', icon='🔎', label='검색', key=('escape', '1')),
            )}}
        )
        result = DockConfig.from_dict(json.loads(json.dumps(config.to_dict())))
        self.assertEqual(result, config)
        self.assertIsInstance(result.dock.width, Width)
        self.assertIsInstance(result.widgets.shortcuts.shortcut(1), Shortcut)
        self.assertIsInstance(result.dock.keys.toggle, KeySequence)

    def test_defaults_and_schema_are_derived_from_fields(self):
        schema = DockConfig.schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(DockConfig().to_dict(), schema)
        jsonschema.validate(DockConfig.initial().to_dict(), schema)
        self.assertEqual(
            schema['properties']['dock']['properties']['keys']['properties']['toggle']['default'],
            DockConfig().dock.keys.toggle.to_list()
        )
        self.assertEqual(schema['properties']['dock']['properties']['width']['default'], Width().to_dict())
        self.assertEqual(
            schema['properties']['widgets']['properties']['shortcuts']['properties']['items']['default'],
            DockConfig().to_dict()['widgets']['shortcuts']['items']
        )
        self.assertEqual(DockConfig.from_dict({}), DockConfig())
        self.assertEqual(DockConfig().widgets.shortcuts.items, ())
        self.assertEqual(len(DockConfig.initial().widgets.shortcuts.items), Shortcut.COUNT)
        self.assertEqual(
            DockConfig.from_dict({'widgets': {'shortcuts': {'items': []}}}).widgets.shortcuts.items,
            ()
        )

    def test_forecast_and_sampling_options_accept_extended_ranges_and_roundtrip(self):
        data = {
            "widgets": {"weather": {"days": 12, "refresh_min": 2880}},
            "media": {"refresh_s": 0.25, "timeout_s": 120},
            "system": {"refresh_s": 300, "history": 10**100},
        }
        config = DockConfig.from_dict(data)
        jsonschema.validate(data, DockConfig.schema())
        self.assertEqual(config.widgets.weather.refresh_s, 2880 * 60)
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore.for_home(directory)
            store.save(config)
            self.assertEqual(store.load(), config)

    def test_width_schema_and_interpretation_share_limits(self):
        for mode, value in (('fixed', 38), ('fixed', 0.6), ('ratio', 0), ('ratio', 1.1)):
            data = {'mode': mode, 'value': value}
            with self.subTest(data=data):
                with self.assertRaises(SettingsError):
                    Width.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, Width.schema())
        for data in ({'value': 1}, {'mode': 'ratio'}):
            with self.assertRaises(SettingsError):
                Width.from_dict(data)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(data, Width.schema())

    def test_invalid_values_are_rejected_at_construction_and_load(self):
        for value in (True, float('nan'), float('inf'), -1, '80'):
            with self.subTest(value=value), self.assertRaises(SettingsError):
                Width(value=value)
        for data in (
            {'typo': 1},
            {'widgets': {'shortcuts': {'items': {}}}},
            {'widgets': {'shortcuts': {'items': [{'slot': 1, 'type': 'shortcut'}]}}},
            {'widgets': {'shortcuts': {'items': [{'slot': True, 'command': 'git', 'type': 'shortcut'}]}}},
            {'widgets': {'shortcuts': {'items': [{'slot': 11, 'command': 'git', 'type': 'shortcut'}]}}},
            {'widgets': {'shortcuts': {'items': [{'slot': 1, 'command': ' ', 'type': 'shortcut'}]}}},
            {'widgets': {'shortcuts': {'items': [{
                'slot': 1,
                'command': 'git',
                'enabled': 'false',
                'type': 'shortcut'
            }]}}},
            {'widgets': {'shortcuts': {'items': [{
                'slot': 1,
                'command': 'git',
                'icon': 'a\nb',
                'type': 'shortcut'
            }]}}},
            {'widgets': {'shortcuts': {'items': [{'slot': 1, 'command': 'git\x00', 'type': 'shortcut'}]}}}
        ):
            with self.subTest(data=data), self.assertRaises(SettingsError):
                DockConfig.from_dict(data)

    def test_duplicate_slots_and_normalized_or_prefix_keys_are_rejected(self):
        with self.assertRaises(SettingsError):
            DockConfig(widgets={'shortcuts': {'items': (Shortcut(1, 'git'), Shortcut(1, 'rg'))}})
        with self.assertRaises(SettingsError):
            DockConfig(widgets={'shortcuts': {'items': (Shortcut(1, 'git', key=('c-w',)),)}})
        with self.assertRaises(SettingsError):
            DockConfig(
                dock={'keys': {'toggle': ('c-x',)}},
                widgets={'shortcuts': {'items': (Shortcut(1, 'git', key=('c-x', 'c-e')),)}}
            )

    def test_lookup_and_display_use_the_same_slot_validation(self):
        config = DockConfig(widgets={'shortcuts': {'items': (Shortcut(1, 'git'),)}})
        for slot in (True, 1.0, 0, 11, '1'):
            with self.subTest(slot=slot):
                with self.assertRaises(SettingsError):
                    config.widgets.shortcuts.shortcut(slot)
                with self.assertRaises(SettingsError):
                    Shortcut.compact_label(slot)

    def test_plain_text_and_editing_keys_remain_available(self):
        for keys in (('1',), ('enter',), ('tab',), ('backspace',), ('escape',), ('bad-key',), ()):
            with self.subTest(keys=keys), self.assertRaises(SettingsError):
                KeySequence(keys)

    def test_explicit_ctrl_combinations_roundtrip_including_terminal_aliases(self):
        for key in ('c-a', 'c-c', 'c-h', 'c-i', 'c-j', 'c-m', 'c-space'):
            with self.subTest(key=key):
                config = DockConfig(widgets={'shortcuts': {'items': (Shortcut(1, 'git', key=(key,)),)}})
                self.assertEqual(DockConfig.from_dict(config.to_dict()), config)

    def test_width_fixed_ratio_resize_and_frame_clamp(self):
        fixed = Width(value=80)
        ratio = Width(mode='ratio', value=0.6)
        self.assertEqual([fixed.resolve(t, t) for t in (120, 160, 60, 160)], [80, 80, 60, 80])
        self.assertEqual([ratio.resolve(t, t) for t in (120, 160)], [72, 96])
        self.assertEqual(Width(mode='ratio', value=1).resolve(120, 118), 118)

    def test_composition_uses_the_entire_raw_buffer(self):
        for command, buffer, expected in (
            ('git', 'status --short', 'git status --short'),
            ('ls -la', '', 'ls -la'),
            ('rg -n ', '"TODO" .', 'rg -n "TODO" .'),
            ('echo', ' hello; pwd', 'echo hello; pwd'),
            ('git', 'status | head\npwd', 'git status | head\npwd'),
            ('echo', '\nhello', 'echo\nhello')
        ):
            with self.subTest(command=command, buffer=buffer):
                self.assertEqual(Shortcut(1, command).compose(buffer), expected)

    def test_config_store_atomic_save_and_failure_preserve_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore.for_home(directory)
            self.assertEqual(store.load(), DockConfig())
            original = DockConfig(widgets={'shortcuts': {'items': (Shortcut(1, 'git', icon='🌿'),)}})
            store.save(original)
            self.assertEqual(store.load(), original)
            before = store.path.read_bytes()
            with patch('dok.settings.store.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    store.save(replace(original, dock=replace(original.dock, width=Width(value=100))))
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(list(store.path.parent.iterdir()), [store.path])

    def test_store_rejects_duplicate_json_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.jsonc'
            path.write_text('{"dock": {}, "dock": {}}')
            with self.assertRaises(SettingsError):
                ConfigStore(path).load()
