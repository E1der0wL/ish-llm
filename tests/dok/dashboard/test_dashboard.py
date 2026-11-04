"""Maximum-mode owners, actual FIGlet art, weather decoding and async lifetime."""
from dok import DockConfig, WidgetsConfig, ClockConfig, DateConfig, TimeConfig
import asyncio
import os
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
import jsonschema
from pyfiglet import Figlet
from dok import ConfigStore, DockMode, SettingsError, WeatherConfig, WeatherLocation, WeekdayConfig
from dok.features.clock.view import WeekdayRenderer
from dok.app.lifecycle import DashboardRuntime
from dok.dashboard.view import DashboardView
from dok.core.state import DockState
from dok.rendering.text import width
from dok.dashboard.layout import DockGeometry
from dok.features.weather.models import ForecastDay, WeatherKind, WeatherSnapshot
from dok.integrations.weather.open_meteo import OpenMeteo
from dok.features.weather.view import WeatherPresentation

class DashboardOwnerTests(unittest.TestCase):

    def test_modes_remember_last_open_size_and_close_is_final(self):
        state = DockState()
        self.assertEqual(state.mode, DockMode.HIDDEN)
        state.toggle()
        self.assertEqual(state.mode, DockMode.MAXIMIZED)
        state.toggle()
        self.assertEqual(state.mode, DockMode.HIDDEN)
        state.toggle()
        self.assertEqual(state.mode, DockMode.MAXIMIZED)
        state.toggle_size()
        self.assertEqual(state.mode, DockMode.BASIC)
        state.close()
        state.toggle_size()
        self.assertFalse(state.requested_visible)

    def test_settings_roundtrip_defaults_and_validation(self):
        config = DockConfig(
            clock=ClockConfig(timezone='Asia/Seoul'),
            widgets=WidgetsConfig(
                date=DateConfig(format='%Y년 %m월 %d일'),
                time=TimeConfig(format='%H:%M:%S'),
                weekday_art=WeekdayConfig(font='small', abbreviated=True),
                weather=WeatherConfig(
                    location=WeatherLocation(37.5665, 126.978, '서울'),
                    temperature_unit='fahrenheit'
                )
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / 'config.jsonc')
            store.save(config)
            self.assertEqual(store.load(), config)
        defaults = DockConfig.from_dict({})
        self.assertIsNone(defaults.widgets.weather.location)
        self.assertFalse(defaults.widgets.weather.can_fetch)
        self.assertEqual(defaults.widgets.weekday_art.font, 'future')
        self.assertIsNone(defaults.clock.timezone)
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        schema = DockConfig.schema()
        for data in (
            {'widgets': {'weekday_art': {'font': 'not-a-font'}}},
            {'clock': {'timezone': 'bad/zone'}},
            {'clock': {'timezone': ''}},
            {'widgets': {'time': {'format': '%n'}}},
            {'widgets': {'date': {'format': '%'}}},
            {'widgets': {'weather': {'location': {'latitude': 91, 'longitude': 0}}}},
            {'widgets': {'weather': {'days': 8}}},
            {'widgets': {'weather': {'refresh_minutes': 0}}},
            {'widgets': {'weather': {'temperature_unit': 'kelvin'}}},
            {'widgets': {'weather': {'icons': {'rain': 'bad\ntext'}}}},
            {'widgets': {'weather': {'style': {'low_temperature_color': 'gray'}}}},
            {'widgets': {'weather': {'width_ratio': 0}}},
            {'widgets': {'weather': {'width_ratio': 1.1}}},
            {'widgets': {'weather': {'icon_offset_columns': 0.5}}},
            {'widgets': {'date': {'style': {'bold': 'true'}}}}
        ):
            with self.subTest(data=data):
                with self.assertRaises(SettingsError):
                    DockConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, schema)
        with self.assertRaises(SettingsError):
            DockConfig(dock={'keys': {'maximize': ('f8',)}})

    def test_null_timezone_roundtrips_and_uses_system_local_time(self):
        config = DockConfig.from_dict({'clock': {'timezone': None}})
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / 'config.jsonc')
            store.save(config)
            self.assertIn('"timezone": null', store.path.read_text(encoding='utf-8'))
            self.assertIsNone(store.load().clock.timezone)
        try:
            for system_zone, offset_hours in (('UTC', 0), ('Asia/Seoul', 9)):
                with self.subTest(system_zone=system_zone), patch.dict(os.environ, {'TZ': system_zone}):
                    time.tzset()
                    local = config.clock.now()
                    self.assertEqual(local.utcoffset().total_seconds(), offset_hours * 3600)
                    self.assertLess(abs((local - datetime.now(timezone.utc)).total_seconds()), 2)
                    self.assertEqual(ClockConfig(timezone='Asia/Seoul').now().utcoffset().total_seconds(), 9 * 3600)
        finally:
            time.tzset()

    def test_calendar_formats_timezone_and_tick_boundaries(self):
        clock = ClockConfig(timezone='Asia/Seoul')
        date = DateConfig(format='%Y-%m-%d')
        time = TimeConfig(format='%H:%M')
        moment = datetime(2026, 11, 3, 6, 29, 45, 500000, tzinfo=timezone.utc)
        self.assertEqual((date.rows(moment)[0].text, time.rows(moment)[0].text), ('2026-11-03', '06:29'))
        self.assertEqual(clock.next_tick(moment, date, time), 14.5)
        self.assertEqual(clock.next_tick(moment, date, replace(time, format='%H:%M:%S')), 0.5)
        self.assertEqual(clock.next_tick(moment, date, replace(time, format='%%S')), 14.5)
        self.assertEqual(clock.now().tzinfo.key, 'Asia/Seoul')
        self.assertEqual(DateConfig(format='').rows(moment), ())
        self.assertEqual(TimeConfig(format='').rows(moment), ())
        date_row, time_row = DateConfig().rows(moment)[0], TimeConfig().rows(moment)[0]
        self.assertEqual((date_row.text, time_row.text), ('03 NOVEMBER, 2026.', '-06:29-'))
        self.assertTrue(date_row.spans[0].bold)
        self.assertFalse(time_row.spans[0].bold)
        self.assertFalse(DateConfig(style={'bold': False}).rows(moment)[0].spans[0].bold)
        self.assertEqual(DateConfig(format='%%B %b %B').rows(moment)[0].text, '%B NOV NOVEMBER')

    def test_figlet_retains_block_alignment_caches_and_fits_all_weekdays(self):
        renderer = WeekdayRenderer()
        config = WeekdayConfig()
        original = Figlet.renderText
        with patch.object(Figlet, 'renderText', autospec=True, side_effect=original) as render:
            lines = renderer.render(config, 1, 80)
            calls = render.call_count
            self.assertEqual(renderer.render(config, 1, 80), lines)
            self.assertEqual(render.call_count, calls)
        expected = Figlet(font=config.font, width=renderer.RENDER_WIDTH).renderText('TUESDAY').splitlines()
        while expected and (not expected[-1].strip()):
            expected.pop()
        self.assertEqual([row.rstrip() for row in lines], [row.rstrip() for row in expected])
        self.assertEqual(len(set(map(width, lines))), 1)
        for day in range(7):
            for available in (2, 20, 39, 80):
                self.assertTrue(all((width(row) <= available for row in renderer.render(config, day, available))))
        self.assertEqual(renderer.render(replace(config, enabled=False), 1, 80), ())

    def test_geometry_falls_back_without_losing_requested_mode(self):
        extra = DashboardView().rows(DockConfig(), WeatherSnapshot(), 80)
        minimum = DockGeometry.height(DockMode.MAXIMIZED, len(extra)) + 1
        self.assertEqual(
            DockGeometry.resolve(DockMode.MAXIMIZED, minimum, 80, len(extra)),
            DockMode.MAXIMIZED
        )
        self.assertEqual(
            DockGeometry.resolve(DockMode.MAXIMIZED, minimum - 1, 80, len(extra)),
            DockMode.BASIC
        )
        self.assertEqual(DockGeometry.resolve(DockMode.MAXIMIZED, 7, 80, len(extra)), DockMode.HIDDEN)
        self.assertEqual(DockGeometry.resolve(DockMode.MAXIMIZED, 50, 38, len(extra)), DockMode.HIDDEN)

    @staticmethod
    def payload():
        return {'daily': {
            'time': ['2026-11-03', '2026-11-04'],
            'weather_code': [61, 0],
            'temperature_2m_max': [18, None],
            'temperature_2m_min': [6, -1]
        }}

    def test_weather_adapter_params_decode_and_presentation(self):
        config = WeatherConfig(location=WeatherLocation(37.5, 127, '서울'), days=2)
        params = OpenMeteo.params(config)
        self.assertEqual(params['latitude'], 37.5)
        self.assertEqual(params['temperature_unit'], 'celsius')
        days = OpenMeteo.decode(self.payload(), 2)
        self.assertEqual(days[0].kind, WeatherKind.RAIN)
        self.assertEqual(days[1].temperatures, '--° -1°')
        moment = datetime(2026, 11, 3, 6, 29, tzinfo=timezone.utc)
        lines = WeatherPresentation.lines(config, WeatherSnapshot(days, moment, 'offline'), 80)
        self.assertNotIn('Open-Meteo', ''.join(lines))
        self.assertNotIn('서울', ''.join(lines))
        self.assertIn(config.texts.unavailable, lines[-1])
        self.assertIn('11/03 06:29', lines[-1])
        self.assertIn('18° 6°', lines[2])
        self.assertTrue(all((width(line) <= 80 for line in lines)))
        for malformed in ({}, {'daily': {}}, {'daily': {'time': []}}):
            with self.assertRaises(ValueError):
                OpenMeteo.decode(malformed, 2)
        self.assertEqual(OpenMeteo.kind(1000), WeatherKind.UNKNOWN)

    def test_compact_weather_colors_icon_offset_and_narrow_width(self):
        config = WeatherConfig(location=WeatherLocation(0, 0), days=2)
        days = OpenMeteo.decode(self.payload(), 2)
        snapshot = WeatherSnapshot(days)
        rows = WeatherPresentation.rows(config, snapshot, 80)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].cells, 40)
        self.assertEqual(rows[1].cells, rows[0].cells)
        self.assertEqual(rows[2].cells, rows[0].cells)
        centered_icons = WeatherPresentation.rows(replace(config, icon_offset_columns=0), snapshot, 80)[1].text
        self.assertEqual(rows[1].text.index(config.icons.rain), centered_icons.index(config.icons.rain) - 1)
        low_spans = [span for span in rows[2].spans if span.color == config.style.low_temperature_color]
        self.assertEqual([span.text for span in low_spans], ['6°', '-1°'])
        for available in (1, 20, 39, 80):
            compact = WeatherPresentation.rows(config, snapshot, available)
            self.assertTrue(all((row.cells <= available for row in compact)))

    def test_no_location_preserves_weekdays_unknown_icons_and_temperature_styles(self):
        today = date(2026, 12, 31)
        stale = WeatherSnapshot(OpenMeteo.decode(self.payload(), 2), error='old failure')
        for icons in ({}, {'unknown': '❔'}):
            config = WeatherConfig(icons=icons, weekday_labels=('M', 'T', 'W', 'Th', 'F', 'Sa', 'Su'))
            rows = WeatherPresentation.rows(config, stale, 80, today=today)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0].text.split(), ['Th', 'F', 'Sa', 'Su', 'M', 'T', 'W'])
            self.assertEqual(rows[1].text.split(), [config.icons.unknown] * config.days)
            self.assertEqual(rows[2].text.count('--° --°'), config.days)
            lows = [span.text for span in rows[2].spans if span.color == config.style.low_temperature_color]
            self.assertEqual(lows, ['--°'] * config.days)
            for available in (0, 1, 20, 39):
                compact = WeatherPresentation.rows(config, stale, available, today=today)
                self.assertTrue(all(row.cells <= available for row in compact))
            self.assertEqual(WeatherPresentation.rows(replace(config, enabled=False), stale, 80), ())

    def test_placeholder_dates_follow_shared_clock_without_other_clock_widgets(self):
        config = DockConfig(widgets={
            'weather': {'days': 2}, 'weekday_art': {'enabled': False},
            'date': {'enabled': False}, 'time': {'enabled': False},
        })
        self.assertTrue(config.widgets.needs_clock)
        self.assertTrue(config.widgets.needs_runtime)
        self.assertFalse(config.widgets.weather.can_fetch)
        view = DashboardView()
        for moment, labels in (
            (datetime(2026, 11, 3, 23, 59), ['ᴛᴜᴇ', 'ᴡᴇᴅ']),
            (datetime(2026, 11, 4, 0, 0), ['ᴡᴇᴅ', 'ᴛʜᴜ']),
        ):
            with patch.object(ClockConfig, 'now', return_value=moment):
                rows = view.rows(config, WeatherSnapshot(), 80)
            self.assertEqual(rows[0].text.split(), labels)

    def test_custom_weather_icons_roundtrip_through_settings_and_render(self):
        icons = {kind.value: f'[{kind.value}]' for kind in WeatherKind}
        config = DockConfig(widgets={'weather': {'icons': icons, 'location': WeatherLocation(0, 0)}})
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / 'config.jsonc')
            store.save(config)
            loaded = store.load()
        jsonschema.validate(loaded.to_dict(), DockConfig.schema())
        for kind in WeatherKind:
            snapshot = WeatherSnapshot((ForecastDay(date(2026, 11, 4), kind, None, None),))
            rows = WeatherPresentation.rows(loaded.widgets.weather, snapshot, 80)
            self.assertEqual(rows[1].text.strip(), icons[kind.value])

class DashboardRuntimeTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tasks = []
        self.calls = 0
        self.fail = False
        self.gate = None
        self.cancelled = False

        async def fetch(config):
            self.calls += 1
            if self.gate is not None:
                try:
                    await self.gate.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            if self.fail:
                raise OSError('offline')
            return (ForecastDay(date(2026, 11, 3), WeatherKind.CLEAR, 18, 6),)

        def create(coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task
        self.app = SimpleNamespace(create_background_task=create)
        self.runtime = DashboardRuntime(lambda: None, SimpleNamespace(fetch=fetch))
        self.config = DockConfig(widgets={'weather': WeatherConfig(location=WeatherLocation(0, 0), days=1)})

    async def asyncTearDown(self):
        self.runtime.close()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.005)

    def sync(self, active=True, config=None):
        self.runtime.sync(active=active, config=config or self.config, app=self.app)

    async def stop(self):
        self.sync(False)
        await self.until(lambda: all((task.done() for task in self.tasks)))

    async def test_no_location_or_disabled_does_not_fetch(self):
        self.sync(config=DockConfig())
        await asyncio.sleep(0.03)
        self.assertEqual(self.calls, 0)
        await self.stop()
        self.sync(config=replace(
            self.config,
            widgets=replace(self.config.widgets, weather=replace(self.config.widgets.weather, enabled=False))
        ))
        await asyncio.sleep(0.03)
        self.assertEqual(self.calls, 0)

    async def test_disabling_all_information_stops_tasks_without_restart_churn(self):
        self.sync()
        await self.until(lambda: bool(self.runtime.snapshot.days))
        config = DockConfig.from_dict({'widgets': {
            'weather': {'enabled': False},
            'weekday_art': {'enabled': False},
            'date': {'enabled': False},
            'time': {'enabled': False}
        }})
        self.sync(config=config)
        await self.until(lambda: all((task.done() for task in self.tasks)))
        count = len(self.tasks)
        for _ in range(4):
            self.sync(config=config)
            await asyncio.sleep(0)
        self.assertEqual(len(self.tasks), count)
        self.assertEqual(self.calls, 1)

    async def test_cache_survives_toggle_and_failure_is_rate_limited(self):
        self.sync()
        await self.until(lambda: bool(self.runtime.snapshot.days))
        saved = self.runtime.snapshot
        await self.stop()
        self.sync()
        await asyncio.sleep(0.03)
        self.assertEqual(self.calls, 1)
        await self.stop()
        self.runtime._weather._next_fetch = 0
        self.fail = True
        self.sync()
        await self.until(lambda: bool(self.runtime.snapshot.error))
        self.assertEqual(self.runtime.snapshot.days, saved.days)
        self.assertEqual(self.runtime.snapshot.updated_at, saved.updated_at)
        for _ in range(3):
            self.sync()
            await asyncio.sleep(0.02)
        self.assertEqual(self.calls, 2)

    async def test_hide_cancels_fetch_and_new_location_cannot_show_old_data(self):
        self.sync()
        await self.until(lambda: bool(self.runtime.snapshot.days))
        changed = replace(
            self.config,
            widgets=replace(
                self.config.widgets,
                weather=replace(self.config.widgets.weather, location=WeatherLocation(1, 2))
            )
        )
        self.assertEqual(self.runtime.snapshot_for(changed), WeatherSnapshot())
        self.gate = asyncio.Event()
        self.sync(config=changed)
        await self.until(lambda: all((task.done() for task in self.tasks)))
        self.sync(config=changed)
        await self.until(lambda: self.calls == 2)
        self.assertFalse(self.runtime.snapshot.days)
        await self.stop()
        self.assertTrue(self.cancelled)

    async def test_http_adapter_uses_async_transport_and_validates_status(self):
        requests = []

        async def respond(request):
            requests.append(request)
            return httpx.Response(200, json=DashboardOwnerTests.payload())
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        config = replace(self.config.widgets.weather, days=2)
        with patch('dok.integrations.weather.open_meteo.httpx.AsyncClient', return_value=client):
            days = await OpenMeteo().fetch(config)
        self.assertEqual(len(days), 2)
        self.assertEqual(requests[0].url.params['timezone'], 'auto')
        self.assertTrue(client.is_closed)
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
        with patch('dok.integrations.weather.open_meteo.httpx.AsyncClient', return_value=client):
            with self.assertRaises(httpx.HTTPStatusError):
                await OpenMeteo().fetch(config)
        self.assertTrue(client.is_closed)
