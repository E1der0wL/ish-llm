"""Current/hourly API decoding, local timeline, unknowns and presentation."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dok.core.theme import Theme
from dok.features.weather.config import WeatherConfig
from dok.features.weather.models import WeatherSnapshot
from dok.features.weather.popup import WeatherPage, WeatherPopupConfig
from dok.integrations.weather.open_meteo import OpenMeteo
from tests.dok.dashboard import test_dashboard as fixtures
from dok.rendering.text import width


class WeatherDetailsTests(unittest.TestCase):
    def test_frame_reads_one_snapshot_and_keeps_selection_when_forecast_window_changes(self):
        snapshot = OpenMeteo.decode_snapshot(fixtures.DashboardOwnerTests.payload(), 2)
        source = Mock(return_value=snapshot)
        clock = SimpleNamespace(now=lambda: snapshot.hours[0].time)
        config = WeatherConfig(location={"lat": 37, "lon": 127})
        page = WeatherPage(source, lambda: config, lambda: clock)
        def draw():
            source.reset_mock()
            text = "\n".join("".join(p[1] for p in row) for row in page.draw_body(108, 40, Theme()))
            source.assert_called_once_with()
            return text
        self.assertIn("8 km/h", draw())
        page.key("right", "")
        self.assertIn("12 km/h", draw())
        self.assertIn("12 km/h", draw())
        source.return_value = replace(snapshot, hours=snapshot.hours[1:])
        self.assertIn("12 km/h", draw())
        source.return_value = replace(snapshot, error="unavailable")
        self.assertIn("cached data may be shown", draw())

    def test_current_hourly_decode_nulls_and_strict_arrays(self):
        data = fixtures.DashboardOwnerTests.payload()
        data["hourly"]["temperature_2m"][1] = None
        snapshot = OpenMeteo.decode_snapshot(data, 2)
        self.assertIsNone(snapshot.hours[1].temperature)
        self.assertEqual(snapshot.current.time.utcoffset(), timedelta(hours=9))
        self.assertEqual(snapshot.hours[0].precipitation_probability, 70)
        self.assertEqual((snapshot.hours[0].wind_speed, snapshot.hours[0].humidity,
                          snapshot.hours[0].rain, snapshot.hours[0].uv_index), (8, 55, .8, 2.5))
        for field, value in (("time", [1, 1]), ("precipitation_probability", [101, 0]), ("weather_code", [1]),
                             ("temperature_2m", [float("nan"), 1]), ("wind_speed_10m", [-1, 0]),
                             ("relative_humidity_2m", [101, 0]), ("rain", [-1, 0]), ("uv_index", [float("nan"), 0])):
            changed = deepcopy(data)
            changed["hourly"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                OpenMeteo.decode_snapshot(changed, 2)

    def test_unix_hour_times_distinguish_dst_fold(self):
        data = fixtures.DashboardOwnerTests.payload()
        data["timezone"] = "America/New_York"
        first = int(datetime(2026,11,1,5,tzinfo=timezone.utc).timestamp())
        data["hourly"]["time"] = [first, first + 3600]
        hours = OpenMeteo.decode_snapshot(data,2).hours
        self.assertEqual([h.time.hour for h in hours], [1,1])
        self.assertNotEqual(hours[0].time.utcoffset(), hours[1].time.utcoffset())

    def test_current_details_palette_probability_and_all_viewport_sizes(self):
        snapshot = OpenMeteo.decode_snapshot(fixtures.DashboardOwnerTests.payload(),2)
        clock = SimpleNamespace(now=lambda:datetime(2026,11,3,tzinfo=timezone.utc))
        config = WeatherConfig(location={"lat":37,"lon":127,"label":"Sample"})
        page = WeatherPage(lambda:snapshot,lambda:config,lambda:clock)
        rows = page.draw_body(108,40,Theme())
        text = "\n".join("".join(p[1] for p in row) for row in rows)
        self.assertIn("Sample",text)
        self.assertIn("Rain chance",text)
        self.assertTrue(any("fg:default" in p[0] for row in rows for p in row if "70%" in p[1]))
        self.assertTrue(any("#94a3b8" in p[0] for row in rows for p in row if p[1] == "0%"))
        for columns, height in ((1,1),(12,4),(40,10),(112,36)):
            rendered = page.draw_body(columns,height,Theme())
            self.assertLessEqual(len(rendered),height)
            self.assertTrue(all(sum(width(p[1]) for p in row) <= columns for row in rendered))

    def test_unknowns_and_configured_figlet_font(self):
        clock = SimpleNamespace(now=lambda:datetime(2026,11,3,tzinfo=timezone.utc))
        page = WeatherPage(WeatherSnapshot,WeatherConfig,lambda:clock,popup=lambda:WeatherPopupConfig(font="small"))
        rows = page.draw_body(108,40,Theme())
        text = "\n".join("".join(p[1] for p in row) for row in rows)
        self.assertIn("Location not configured", text)
        self.assertIn("--%",text)
        self.assertIn("--°",text)
        page.key("tab", "")
        self.assertTrue(page.status_text().startswith("Buttons"))
        self.assertTrue(page.key("escape", ""))
        self.assertTrue(page.status_text().startswith("Time"))
        self.assertTrue(page.key("escape", ""))  # Root stays open.

    def test_graph_constant_values_and_missing_values_do_not_invent_connections(self):
        flat = WeatherPage.graph([2,2,2],8,5)
        self.assertTrue(any("─" in row for row in flat))
        missing = WeatherPage.graph([2,None,3],8,5)
        self.assertTrue(all(row[8:16] == " "*8 for row in missing))

    def test_selected_hour_details_and_area_key_hints_follow_navigation(self):
        snapshot = OpenMeteo.decode_snapshot(fixtures.DashboardOwnerTests.payload(), 2)
        clock = SimpleNamespace(now=lambda: snapshot.hours[0].time)
        config = WeatherConfig(location={"lat": 37, "lon": 127})
        page = WeatherPage(lambda: snapshot, lambda: config, lambda: clock)
        def draw():
            return "\n".join("".join(p[1] for p in row) for row in page.draw_body(108, 40, Theme()))
        self.assertIn("8 km/h", draw())
        page.key("right", "")
        self.assertIn("12 km/h", draw())
        self.assertIn("60 %", draw())
        self.assertIn(snapshot.hours[1].time.strftime("%m/%d %H:%M"), page.status_text())
        self.assertNotIn("Updated:", draw())
        self.assertNotIn("Open-Meteo", draw())
        page.key("tab", "")
        self.assertTrue(page.status_text().startswith("Buttons"))
        page.key("enter", "")  # Now, through the focused button.
        self.assertIn("8 km/h", draw())
        page.key("tab", "")
        self.assertTrue(page.status_text().startswith("Time"))
