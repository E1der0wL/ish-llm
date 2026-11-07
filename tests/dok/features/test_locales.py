"""Language ownership, fallback, custom text and selected-hour dates."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from string import Formatter
from types import SimpleNamespace
import unittest

import jsonschema

from dok import DockConfig, Locale, Language, SettingsError
from dok.locales import ko
from dok.locales.messages import Message as M
from dok.features.calendar.view import CalendarPage, Confirmation
from dok.features.calendar.editor import EditorField
from dok.features.weather.models import WeatherSnapshot, WeatherHour, WeatherKind
from dok.features.weather.popup import WeatherPage
from dok.features.media.config import TitleConfig
from dok.features.toolbar.config import InputItem, NumbersConfig, ToolbarDate
from dok.features.toolbar.editor import EditorSnapshot, NumberSummary
from dok.core.theme import Theme
from dok.settings.jsonc import Jsonc
from dok.features.weather.view import WeatherPresentation


class LocaleTests(unittest.TestCase):
    def test_language_schema_fallback_and_pack_placeholders(self):
        self.assertIsNone(DockConfig().language)
        for value in (None, "en", "ko"):
            config = DockConfig(language=value)
            jsonschema.validate(config.to_dict(), DockConfig.schema())
            self.assertEqual(DockConfig.from_dict(config.to_dict()), config)
        with self.assertRaises(SettingsError):
            DockConfig(language="unsupported")
        for code in (None, "en-US", "fr", "C.UTF-8"):
            self.assertEqual(Locale.resolve(code).language, Language.ENGLISH)
        for code in ("ko", "ko-KR", "ko_KR.UTF-8"):
            self.assertEqual(Locale.resolve(code).language, Language.KOREAN)
        self.assertEqual(set(ko.TEXT), set(M))
        def fields(text):
            return {name for _, name, _, _ in Formatter().parse(text) if name is not None}
        for message in M:
            self.assertEqual(fields(message.value), fields(ko.TEXT[message]), message)

    def test_defaults_localize_but_custom_text_and_empty_strings_are_preserved(self):
        en, korean = Locale(), Locale.resolve("ko")
        title = TitleConfig()
        self.assertEqual(title.application_caption(("My player",), en), "Now playing: My player")
        self.assertEqual(title.application_caption(("My player",), korean), "재생 중 : My player")
        self.assertEqual(title.audio_error_caption("backend", korean), "사운드 연결 실패")
        self.assertTrue(InputItem().render(EditorSnapshot(), 20, locale=korean).startswith("행:"))
        self.assertTrue(InputItem(label="Custom:").render(EditorSnapshot(), 20, locale=korean).startswith("Custom:"))
        self.assertIn("합계:", NumbersConfig().render(NumberSummary(), locale=korean))
        day = datetime(2026,11,7)
        self.assertIn("(토)", ToolbarDate().render(day, korean))
        self.assertIn("(X)", ToolbarDate(weekdays=("X",)*7).render(day, korean))

    def test_status_messages_are_absent_from_settings_and_follow_language_everywhere(self):
        config = DockConfig(widgets={"weather": {"location": {"lat": 0, "lon": 0}}})
        for name in ('title', 'weather'):
            self.assertNotIn('texts', config.to_dict()['widgets'][name])
            self.assertNotIn('texts', DockConfig.schema()['properties']['widgets']['properties'][name]['properties'])
            raw = {'widgets': {name: {'texts': {}}}}
            with self.assertRaises(SettingsError):
                DockConfig.from_dict(raw)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(raw, DockConfig.schema())
        self.assertNotIn('"texts"', Jsonc.dumps(DockConfig.initial()))
        snapshot = WeatherSnapshot(error='backend diagnostic')
        clock = SimpleNamespace(now=lambda: datetime(2026,11,7))
        for code, expected in (('en', 'Weather connection failed'), ('ko', '날씨 연결 실패')):
            locale = Locale.resolve(code)
            rows = WeatherPresentation.lines(config.widgets.weather, snapshot, 80, locale=locale)
            self.assertIn(expected, '\n'.join(rows))
            page = WeatherPage(lambda: snapshot, lambda: config.widgets.weather, lambda: clock, locale=lambda: locale)
            rendered = '\n'.join(''.join(part[1] for part in row) for row in page.draw_body(112,60,Theme()))
            self.assertIn(expected, rendered)
            self.assertNotIn(snapshot.error, rendered)

    def test_hour_date_tracks_midnight_selection_and_language_without_state_reset(self):
        first = datetime(2026,11,7,23,tzinfo=timezone(timedelta(hours=9)))
        hours = tuple(WeatherHour(first + timedelta(hours=i), 10, 9, WeatherKind.CLEAR, 0) for i in range(2))
        snapshot = WeatherSnapshot(hours=hours)
        config = DockConfig()
        locale = Locale()
        clock = SimpleNamespace(now=lambda: first)
        page = WeatherPage(lambda: snapshot, lambda: config.widgets.weather, lambda: clock, locale=lambda: locale)
        def lines():
            return ["".join(p[1] for p in row).strip() for row in page.draw_body(112, 60, Theme())]
        before = lines()
        self.assertEqual(before[next(i for i,x in enumerate(before) if x.startswith("Time")) - 1], locale.date(first.date()))
        page.key("right", "")
        after = lines()
        self.assertIn(locale.date(hours[1].time.date()), after)
        locale = Locale.resolve("ko")
        korean = lines()
        self.assertIn("2026년 11월 8일 (일)", korean)
        self.assertTrue(any("강수확률" in row for row in korean))
        self.assertEqual(page._selected_time, hours[1].time)
        snapshot = WeatherSnapshot()
        unknown = lines()
        self.assertEqual(unknown[next(i for i,x in enumerate(unknown) if x.startswith("시간대")) - 1], "--")

    def test_calendar_language_switch_keeps_draft_confirmation_and_actions(self):
        config = DockConfig()
        clock = SimpleNamespace(now=lambda: datetime(2026,11,7), timezone="UTC")
        locale = Locale()
        page = CalendarPage(lambda: clock, lambda: config.popups.calendar, locale=lambda: locale)
        page.key("enter", "")
        page.key("<any>", "내 일정")
        self.assertTrue(page.status_text().startswith("Title"))
        self.assertFalse(page.request_close())
        self.assertEqual(page.confirm, Confirmation.SAVE)
        locale = Locale.resolve("ko")
        self.assertTrue(page.status_text().startswith("확인"))
        self.assertEqual(page.draft.buffer(EditorField.TITLE).text, "내 일정")
        text = "\n".join("".join(p[1] for p in row) for row in page.draw_body(136,44,Theme()))
        self.assertIn("변경 사항을 저장할까요?", text)
        page.key("escape", "")
        self.assertTrue(page.status_text().startswith("제목"))
        self.assertEqual(page.draft.buffer(EditorField.TITLE).text, "내 일정")
