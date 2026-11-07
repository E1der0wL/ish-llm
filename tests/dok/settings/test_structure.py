"""The single settings shape, owner defaults and rejection of removed paths."""
import unittest
from pathlib import Path

import jsonschema

from dok import DockConfig, SettingsError
from dok.core.records import Record
from dok.settings.jsonc import Jsonc
from dok.features.weather.config import WeatherLocation


class StructureTests(unittest.TestCase):
    def test_compact_settings_have_no_old_aliases(self):
        config = DockConfig()
        cases = (
            (config.widgets, ("weekday_art", "media_controls")),
            (config.widgets.visualizer, ("width_columns",)),
            (config.widgets.title.spacing, ("after_rows", "basic_after_rows")),
            (config.widgets.shortcuts.spacing, ("item_gap_columns",)),
            (config.widgets.weather, ("temperature_unit", "refresh_minutes", "icon_offset_columns", "weekday_labels")),
            (config.widgets.weather.style, ("low_temperature_color",)),
            (WeatherLocation(0, 0), ("latitude", "longitude")),
            (config.media, ("refresh_seconds", "timeout_seconds")),
            (config.system, ("refresh_seconds", "history_samples")),
            (config.toolbar, ("group_gap_columns", "padding_left_columns", "padding_right_columns")),
            (config.toolbar.date, ("hide_when_dock_visible",)),
            (config.toolbar.regions.size, ("width_columns", "padding_before_columns", "padding_after_columns", "item_gap_columns", "alignment", "max_digits")),
            (config.toolbar.numbers, ("decimal_places", "value_width_columns", "empty_text", "limit_text")),
            (config.toolbar.system.cpu.graph, ("columns",)),
            (config.popups.input, ("escape_seconds",)),
            (config.popups.notifications, ("duration_seconds",)),
            (config.popups.calendar, ("first_weekday", "reminders_enabled", "reminder_poll_seconds")),
            (config.popups.weather, ("hour_columns", "graph_rows")),
            (config.theme.roles, ("button_background", "editor_background", "input_background", "selection_foreground", "selection_background", "secondary_text", "low_temperature")),
        )
        for record, removed in cases:
            validator = jsonschema.Draft202012Validator(type(record).schema())
            for name in removed:
                data = dict(record.to_dict(), **{name: None})
                with self.subTest(owner=type(record).__name__, name=name):
                    with self.assertRaises(SettingsError):
                        type(record).from_dict(data)
                    self.assertFalse(validator.is_valid(data))

    def test_example_is_generated_from_current_owners(self):
        example = Path(__file__).resolve().parents[3] / "docs/dok/config.example.jsonc"
        self.assertEqual(example.read_text(encoding="utf-8"), Jsonc.dumps(DockConfig.initial()))

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
            {"widgets": {"weather": {"temperature_unit": "celsius"}}},
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
            "date": {"style": {"color": "cyan"}},
            "time": {"format": "%H:%M:%S", "style": {"color": "blue"}},
            "title": {"spacing": {"after": 4}},
            "controls": {"spacing": {"basic_after": None}},
            "shortcuts": {"spacing": {"gap": 3}},
        }})
        self.assertTrue(config.widgets.date.style.bold)
        self.assertEqual(config.widgets.title.spacing.basic_after, 0)
        self.assertEqual(config.widgets.controls.spacing.after, 2)
        self.assertEqual(config.widgets.shortcuts.spacing.after, 1)
        self.assertNotEqual(config.widgets.date.style.color, config.widgets.time.style.color)
        self.assertEqual(DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(config))), config)

    def test_generated_root_and_widget_order_matches_display(self):
        data = Jsonc.loads(Jsonc.dumps(DockConfig()))
        self.assertEqual(list(data), ["dock", "audio", "clock", "widgets", "media", "system", "toolbar", "popups", "theme", "language"])
        # Adding theme settings must not shift existing positional constructors.
        self.assertEqual(DockConfig({"width": {"value": 90}}).dock.width.value, 90)
        self.assertEqual(
            list(data["widgets"]),
            [
                "weather",
                "weekday",
                "date",
                "time",
                "visualizer",
                "progress",
                "title",
                "controls",
                "shortcuts"
            ]
        )
        self.assertIsNone(data["widgets"]["weather"]["location"])


if __name__ == "__main__":
    unittest.main()
