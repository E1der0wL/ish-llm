"""Popup settings, visible-time queue and feature content contracts."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest

import jsonschema

from dok import DockConfig, PopupContent, PopupDimension, PopupPanel, SettingsError
from dok.features.calendar.config import CalendarConfig
from dok.features.calendar.view import CalendarPage
from dok.features.weather.config import WeatherConfig
from dok.features.weather.models import ForecastDay, WeatherKind, WeatherSnapshot
from dok.features.weather.popup import WeatherPage
from dok.popups.models import Notification, NotificationQueue
from dok.popups.config import PopupInput
from dok.settings.jsonc import Jsonc
from dok.core.theme import Theme


class PopupContractsTests(unittest.TestCase):
    def test_escape_timing_schema_and_runtime_validation_match(self):
        for value in (.001, .05, 2):
            jsonschema.validate({"escape_s": value}, PopupInput.schema())
            self.assertEqual(PopupInput(escape_s=value).escape_s, value)
        for value in (0, -1, True, "0.05"):
            with self.subTest(value=value):
                with self.assertRaises(SettingsError):
                    PopupInput(escape_s=value)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate({"escape_s": value}, PopupInput.schema())

    def test_dimension_schema_and_decoder_agree_including_omitted_mode(self):
        for data in ({}, {"value": 9000}, {"mode": "ratio", "value": .7}):
            PopupDimension.from_dict(data)
            jsonschema.validate(data, PopupDimension.schema())
        for data in ({"value": 0}, {"value": 1.5}, {"mode": "ratio"}, {"mode": "ratio", "value": 2}):
            with self.subTest(data=data):
                with self.assertRaises(SettingsError):
                    PopupDimension.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, PopupDimension.schema())

    def test_large_spacing_is_accepted_and_geometry_stays_inside_viewport(self):
        panel = PopupPanel.from_dict({"width": {"value": 100000}, "height": {"value": 100000},
            "margin": {"top": 100000, "right": 100000, "bottom": 100000, "left": 100000},
            "padding": {"top": 100000}})
        for w, h in ((0, 0), (1, 1), (5, 3), (120, 30)):
            for notification in (False, True):
                rect = panel.fit(w, h, notification=notification)
                self.assertGreaterEqual(rect.left, 0)
                self.assertGreaterEqual(rect.top, 0)
                self.assertLessEqual(rect.left + rect.width, w)
                self.assertLessEqual(rect.top + rect.height, h)

    def test_generated_settings_share_defaults_comments_and_key_validation(self):
        config = DockConfig.initial()
        self.assertEqual(DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(config))), config)
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        self.assertEqual(config.popups.general.style.color, "foreground")
        self.assertEqual((config.popups.notifications.panel.padding.top,
                          config.popups.notifications.panel.padding.bottom), (1, 1))
        self.assertEqual(config.popups.notifications.panel.height.value, 10)
        partial = DockConfig(popups={"notifications": {"panel": {"padding": {"left": 3}}}})
        self.assertEqual((partial.popups.notifications.panel.padding.top,
                          partial.popups.notifications.panel.padding.bottom), (1, 1))
        self.assertEqual((config.popups.general.padding.top, config.popups.general.padding.bottom), (0, 0))
        self.assertFalse(config.dock.mouse.enabled)
        with self.assertRaises(SettingsError):
            DockConfig(popups={"calendar": {"key": ["c-w"]}})
        with self.assertRaises(SettingsError):
            DockConfig(popups={"weather": {"key": ["escape", "c"]}})

    def test_queue_pauses_without_expiring_unseen_notifications(self):
        queue = NotificationQueue()
        first = Notification(PopupContent.plain("First", "body"), 6)
        second = Notification(PopupContent.plain("Second", "body"), None)
        queue.add(first)
        queue.add(second)
        queue.sync(False, 100)
        self.assertIsNone(queue.current)
        queue.sync(True, 200)
        self.assertIs(queue.current, first)
        queue.sync(False, 202)
        self.assertEqual(queue.remaining, 4)
        queue.sync(False, 300)
        self.assertEqual(queue.remaining, 4)
        queue.sync(True, 400)
        queue.sync(True, 404)
        self.assertIs(queue.current, second)
        queue.sync(True, 10000)
        self.assertIs(queue.current, second)
        queue.dismiss()
        self.assertEqual(queue.pending, 0)

    def test_calendar_leap_month_navigation_and_clock_timezone_date(self):
        clock = SimpleNamespace(now=lambda: datetime(2024, 2, 29, 0, 1, tzinfo=timezone.utc))
        page = CalendarPage(lambda: clock, lambda: CalendarConfig(week_start=6))

        def check_header(month):
            self.assertEqual(page.content().title, "Calendar")
            text = "\n".join("".join(p[1] for p in row) for row in page.draw_body(132, 42, Theme()))
            self.assertIn(month, text)
            return text

        content = page.content()
        text = check_header("February 2024")
        self.assertIn("Sun", text)
        self.assertIn("29 •", text)
        content.actions[2].invoke()
        check_header("March 2024")
        self.assertEqual(page.selected, date(2024, 3, 29))
        page.content().actions[1].invoke()
        check_header("February 2024")
        self.assertEqual(page.selected, date(2024, 2, 29))
        clock.now = lambda: datetime(2025, 1, 1)
        page.content().actions[1].invoke()
        page.content().actions[0].invoke()
        check_header("December 2024")
        self.assertEqual(page.selected, date(2024, 12, 1))

    def test_weather_uses_current_config_and_snapshot_with_gray_low_temperature(self):
        clock = SimpleNamespace(now=lambda: datetime(2026, 11, 7))
        config = WeatherConfig(icons={"unknown": "?"})
        snapshot = WeatherSnapshot()
        page = WeatherPage(lambda: snapshot, lambda: config, lambda: clock)
        text = "\n".join(row.text for row in page.content().rows)
        self.assertIn("High --°   Low --°", text)
        self.assertIn("Set widgets.weather.location", text)
        snapshot = WeatherSnapshot((ForecastDay(date(2026, 11, 7), WeatherKind.CLEAR, 18, 4),
                                    ForecastDay(date(2026, 11, 8), WeatherKind.RAIN, 15, 3)), clock.now())
        self.assertIn("High 18°", page.content().rows[2].text)
        self.assertEqual(page.content().rows[2].spans[-1].color, config.style.low_color)
        page.content().actions[1].invoke()
        self.assertIn("08 November", page.content().rows[0].text)
        self.assertIn("Rain", page.content().rows[1].text)
