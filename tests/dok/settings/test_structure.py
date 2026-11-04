"""The single settings shape, owner defaults and rejection of removed paths."""
import unittest

import jsonschema

from dok import DockConfig, SettingsError
from dok.core.records import Record
from dok.settings.jsonc import Jsonc


class StructureTests(unittest.TestCase):
    def test_only_current_paths_are_accepted_by_owner_and_schema(self):
        removed = (
            {"width": {"value": 80}},
            {"toggle_key": ["f8"]},
            {"dock": {"keys": {"maximize": ["f9"]}}},
            {"widgets": {"weather": {"texts": {"location_missing": "removed"}}}},
            {"shortcuts": []},
            {"shortcut_layout": {}},
            {"dashboard": {}},
            {"widgets": {"transport": {}}},
            {"widgets": {"visualizer": {"length": 38}}},
            {"widgets": {"title": {"color": "#ffffff"}}},
            {"widgets": {"title": {"padding_after": 1}}},
            {"widgets": {"weather": {"unit": "celsius"}}},
            {"widgets": {"shortcuts": {"items": [{"slot": 1, "command": "git"}]}}},
            {"widgets": {"shortcuts": {"items": [{
                "slot": 1,
                "type": "shortcut",
                "command": "git",
                "emoji": "x"
            }]}}},
            {"widgets": {"shortcuts": {"items": [{"slot": 1, "kind": "separator"}]}}},
        )
        schema = DockConfig.schema()
        for data in removed:
            with self.subTest(data=data):
                with self.assertRaises(SettingsError):
                    DockConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, schema)

    def test_empty_nested_objects_keep_each_owners_defaults(self):
        def check(record):
            for field in record.documented_fields():
                if isinstance(field.value, Record):
                    with self.subTest(owner=type(record).__name__, field=field.name):
                        self.assertEqual(type(record).from_dict({field.name: {}}), type(record)())
                    check(field.value)
        check(DockConfig())

    def test_partial_styles_spacing_and_independent_date_time(self):
        config = DockConfig.from_dict({"widgets": {
            "date": {"style": {"color": "#aabbcc"}},
            "time": {"format": "%H:%M:%S", "style": {"color": "#112233"}},
            "title": {"spacing": {"after_rows": 4}},
            "media_controls": {"spacing": {"basic_after_rows": None}},
            "shortcuts": {"spacing": {"item_gap_columns": 3}},
        }})
        self.assertTrue(config.widgets.date.style.bold)
        self.assertEqual(config.widgets.title.spacing.basic_after_rows, 0)
        self.assertEqual(config.widgets.media_controls.spacing.after_rows, 2)
        self.assertEqual(config.widgets.shortcuts.spacing.after_rows, 1)
        self.assertNotEqual(config.widgets.date.style.color, config.widgets.time.style.color)
        self.assertEqual(DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(config))), config)

    def test_generated_root_and_widget_order_matches_display(self):
        data = Jsonc.loads(Jsonc.dumps(DockConfig()))
        self.assertEqual(list(data), ["dock", "audio", "clock", "widgets"])
        self.assertEqual(
            list(data["widgets"]),
            [
                "weather",
                "weekday_art",
                "date",
                "time",
                "visualizer",
                "progress",
                "title",
                "media_controls",
                "shortcuts"
            ]
        )
        self.assertIsNone(data["widgets"]["weather"]["location"])


if __name__ == "__main__":
    unittest.main()
