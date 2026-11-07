"""Element layout settings agree across schema, persistence and geometry."""
from dok import DockConfig, ClockConfig, DateConfig, TimeConfig
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import jsonschema
from dok import ConfigStore, DockMode, ElementConfig, SettingsError
from dok.dashboard.view import DashboardView
from dok.dashboard.layout import DockGeometry
from dok.dashboard.widgets import DockRow
from dok.features.weather.models import ForecastDay, WeatherKind, WeatherSnapshot
from dok.features.weather.config import WeatherConfig, WeatherLocation
from dok.features.weather.view import WeatherPresentation
from dok.rendering.text import width

class ElementTests(unittest.TestCase):

    def test_settings_validate_and_roundtrip_through_the_same_owners(self):
        data = {'widgets': {
            'visualizer': {'enabled': False, 'spacing': {'after': 4}},
            'progress': {'spacing': {'after': 2}},
            'title': {'spacing': {'after': 3, 'basic_after': None}},
            'controls': {'enabled': False},
            'shortcuts': {'spacing': {'after': 0, 'gap': 2}},
            'weather': {'enabled': False, 'spacing': {'after': 3}},
            'weekday': {'spacing': {'after': 0}},
            'date': {'enabled': False},
            'time': {'spacing': {'after': 4}}
        }}
        config = DockConfig.from_dict(data)
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / 'config.jsonc')
            store.save(config)
            self.assertEqual(store.load(), config)
        self.assertEqual(config.widgets.title.spacing.for_mode(DockMode.BASIC), 3)
        self.assertEqual(config.widgets.title.spacing.for_mode(DockMode.MAXIMIZED), 3)
        for owner in (
            ElementConfig,
            type(config.widgets.visualizer),
            type(config.widgets.progress),
            type(config.widgets.title),
            type(config.widgets.controls),
            type(config.widgets.shortcuts),
            type(config.widgets.weather),
            type(config.widgets.weekday)
        ):
            for data in (
                {'enabled': 'false'},
                {'spacing': {'after': -1}},
                {'spacing': {'after': True}},
                {'spacing': {'basic_after': 0.5}}
            ):
                with self.subTest(owner=owner, data=data):
                    with self.assertRaises(SettingsError):
                        owner.from_dict(data)
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.validate(data, owner.schema())

    def test_disabled_body_elements_collapse_padding_and_allow_shorter_terminals(self):
        config = DockConfig.from_dict({'widgets': {
            'visualizer': {'enabled': False, 'spacing': {'after': 20}},
            'title': {'enabled': False, 'spacing': {'basic_after': 20}}
        }})
        self.assertEqual(
            DockGeometry.body_rows(DockMode.BASIC, config),
            (DockRow.PROGRESS, DockRow.TRANSPORT, None, DockRow.SHORTCUTS, None)
        )
        self.assertEqual(DockGeometry.resolve(DockMode.BASIC, 6, 80, 0, config), DockMode.BASIC)
        self.assertEqual(DockGeometry.resolve(DockMode.BASIC, 5, 80, 0, config), DockMode.HIDDEN)

    def test_date_time_and_dashboard_gaps_are_independent(self):
        config = DockConfig.from_dict({'widgets': {
            'weather': {'enabled': False, 'spacing': {'after': 20}},
            'weekday': {'enabled': False, 'spacing': {'after': 20}},
            'date': {'spacing': {'after': 2}},
            'time': {'enabled': False, 'spacing': {'after': 20}}
        }})
        now = datetime(2026, 11, 3, 6, 29, tzinfo=timezone.utc)
        with patch.object(ClockConfig, 'now', return_value=now):
            rows = DashboardView().rows(config, WeatherSnapshot(), 80)
            self.assertEqual(tuple((row.text for row in rows)), ('03 NOVEMBER, 2026.', '', ''))
            self.assertTrue(rows[0].spans[0].bold)
            widgets = replace(
                config.widgets,
                date=DateConfig(enabled=False),
                time=TimeConfig(spacing={'after': 1})
            )
            rows = DashboardView().rows(replace(config, widgets=widgets), WeatherSnapshot(), 80)
            self.assertEqual(tuple((row.text for row in rows)), ('-06:29-', ''))
        self.assertEqual(
            config.clock.next_tick(now, widgets.date, TimeConfig(format='%S', enabled=False)),
            60
        )

    def test_dashboard_only_and_completely_empty_layouts(self):
        config = DockConfig.from_dict({'widgets': {name: {'enabled': False} for name in (
            'visualizer',
            'progress',
            'title',
            'controls',
            'shortcuts'
        )}})
        self.assertEqual(DockGeometry.height(DockMode.BASIC, config=config), 0)
        self.assertEqual(DockGeometry.resolve(DockMode.MAXIMIZED, 5, 80, 4, config), DockMode.MAXIMIZED)
        self.assertEqual(DockGeometry.resolve(DockMode.MAXIMIZED, 4, 80, 4, config), DockMode.HIDDEN)
        self.assertEqual(DockGeometry.resolve(DockMode.MAXIMIZED, 50, 80, 0, config), DockMode.HIDDEN)

    def test_weather_weekday_labels_default_to_small_capitals_and_follow_dates(self):
        config = WeatherConfig(location=WeatherLocation(0, 0))
        monday = date(2026, 11, 2)
        self.assertIsNone(config.weekdays)
        defaults = tuple(config.weekday_label(monday + timedelta(days=i)) for i in range(7))
        self.assertEqual(defaults, ('ᴍᴏɴ', 'ᴛᴜᴇ', 'ᴡᴇᴅ', 'ᴛʜᴜ', 'ꜰʀɪ', 'ꜱᴀᴛ', 'ꜱᴜɴ'))
        self.assertTrue(all((width(label) == 3 for label in defaults)))
        days = tuple((ForecastDay(monday + timedelta(days=index), WeatherKind.CLEAR, 18, 6) for index in range(7)))
        self.assertEqual(
            WeatherPresentation.rows(config, WeatherSnapshot(days), 80)[0].text.split(),
            list(defaults)
        )
        labels = ('월', '화', '수', '목', '금', '토', '일')
        config = replace(config, weekdays=labels)
        self.assertEqual(config.weekday_label(monday + timedelta(days=6)), '일')
        self.assertEqual(config.weekday_label(monday + timedelta(days=7)), '월')
        self.assertEqual(WeatherConfig.from_dict(config.to_dict()), config)
        for labels in (('MON',), ('MON',) * 8, ('MON',) * 6 + ('',), ('MON',) * 6 + ('bad\ntext',)):
            with self.subTest(labels=labels):
                data = {'weekdays': list(labels)}
                with self.assertRaises(SettingsError):
                    WeatherConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, WeatherConfig.schema())
