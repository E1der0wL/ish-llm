"""Approved popup geometry, focus feedback and bounded terminal output."""
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prompt_toolkit.formatted_text.utils import split_lines
from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.styles import Style

from dok import DockConfig
from dok.features.calendar.editor import EditorField
from dok.features.calendar.view import CalendarPage
from dok.features.calendar.models import CalendarEvent, Recurrence
from dok.features.calendar.reminders import Reminder, ReminderUnit
from dok.features.calendar.store import CalendarSnapshot
from dok.features.weather.models import WeatherSnapshot
from dok.features.weather.popup import WeatherPage
from dok.popups.config import PopupRect
from dok.rendering.popups import PopupSurface
from dok.rendering.text import width


class PopupLayoutTests(unittest.TestCase):
    def setUp(self):
        self.config = DockConfig.initial()
        self.clock = SimpleNamespace(now=lambda: datetime(2026,11,7), timezone="UTC")
        self.surface = PopupSurface(lambda: None, lambda: None, modal=True)

    def draw(self, page, panel, columns, rows):
        self.surface.page = page
        self.surface.draw(page.content(), panel, PopupRect(0,0,columns,rows), theme=self.config.theme)
        parts = list(split_lines(self.surface.fragments))
        return parts, ["".join(p[1] for p in row) for row in parts]

    @staticmethod
    def inner(line):
        return line.removeprefix("│").removesuffix("│").strip()

    def calendar(self):
        page = CalendarPage(lambda: self.clock, lambda: self.config.popups.calendar)
        page.key("enter", "")
        page.key("<any>", "Sample")
        return page

    def test_calendar_fixed_status_padding_editor_background_and_focus_color(self):
        page = self.calendar()
        parts, lines = self.draw(page, self.config.popups.calendar.panel, 136, 44)
        self.assertEqual(len(lines), 44)
        self.assertFalse(self.inner(lines[2]))  # Below title.
        self.assertFalse(self.inner(lines[-3]))  # Above status.
        self.assertIn("Title ·", lines[-2])
        self.assertIn("Today 2026-11-07", "\n".join(lines))
        self.assertNotIn("New", "\n".join(lines))
        self.assertNotIn("Events", "\n".join(lines))
        self.assertIn(page.TIME_ARROW, "\n".join(lines))
        self.assertNotIn("->", "\n".join(lines))
        self.assertTrue(any("bg:#475569" in p[0] for row in parts for p in row if "Sample" in p[1]))
        before = next(p[0] for row in parts for p in row if p[1].strip() == "Save")
        page.field = EditorField.SAVE
        focused, lines = self.draw(page, self.config.popups.calendar.panel, 136, 44)
        after = next(p[0] for row in focused for p in row if p[1].strip() == "Save")
        style = Style([])
        self.assertEqual(style.get_attrs_for_style_str(before).bgcolor, style.get_attrs_for_style_str(after).bgcolor)
        self.assertEqual(style.get_attrs_for_style_str(after).color, self.config.theme.resolve("title")[1:])
        self.assertIn("Save ·", lines[-2])
        page.field = EditorField.ALL_DAY
        page.key("enter", "")
        _, lines = self.draw(page, self.config.popups.calendar.panel, 136, 44)
        self.assertIn("● All day", "\n".join(lines))
        self.assertIn(page.TIME_ARROW, "\n".join(lines))
        self.assertNotIn("->", "\n".join(lines))
        self.assertNotIn("09:00", "\n".join(lines))
        self.assertNotIn("10:00", "\n".join(lines))

    def test_weather_spacing_buttons_and_status_bar(self):
        page = WeatherPage(WeatherSnapshot, lambda: self.config.widgets.weather, lambda: self.clock)
        _, lines = self.draw(page, self.config.popups.weather.panel, 112, 36)
        self.assertEqual(len(lines), 36)
        first_apparent = next(i for i, line in enumerate(lines) if "Feels like" in line)
        probability = next(i for i, line in enumerate(lines) if "Rain chance" in line)
        self.assertFalse(self.inner(lines[first_apparent + 1]))
        self.assertFalse(self.inner(lines[probability + 1]))
        button = next(i for i, line in enumerate(lines) if "Now" in line)
        self.assertFalse(self.inner(lines[button - 1]))
        self.assertFalse(self.inner(lines[button + 1]))
        self.assertIn("Time", lines[-2])
        page.key("tab", "")
        _, lines = self.draw(page, self.config.popups.weather.panel, 112, 36)
        self.assertIn("Buttons ·", lines[-2])

    def test_reminder_registration_list_and_start_number_style(self):
        page = self.calendar()
        page.field = EditorField.ALARM_ADD
        page.key("enter", "")
        self.assertEqual(page.draft.alarms, (Reminder("minutes", 5),))
        page.field = EditorField.ALARM_UNIT
        page.key("enter", "")
        page.key("home", "")
        page.key("enter", "")
        self.assertEqual(page.draft.alarm_unit, ReminderUnit.START)
        page.key("s-tab", "")
        self.assertEqual(page.field, EditorField.HOLIDAY)  # No repeat limit and no editable alarm number.
        page.field = EditorField.ALARM_ADD
        page.key("enter", "")
        parts, lines = self.draw(page, self.config.popups.calendar.panel, 136, 44)
        self.assertIn("5 minutes before", "\n".join(lines))
        self.assertIn("At event start", "\n".join(lines))
        register = next(i for i, line in enumerate(lines) if " Add " in line)
        self.assertFalse(lines[register + 1].split("│")[-2].strip())
        number = next(p for p in parts[register] if p[1].strip() == "5")
        self.assertEqual(Style([]).get_attrs_for_style_str(number[0]).color,
                         self.config.theme.resolve("comment")[1:])
        self.assertEqual(len(number), 2)  # Read-only number has no mouse action.
        page.field = EditorField.ALARMS
        page.key("home", "")
        page.key("delete", "")
        self.assertEqual(page.draft.alarms, (Reminder("start", 0),))

    def test_reminder_list_scrolls_without_limiting_registered_count(self):
        page = self.calendar()
        page.draft.alarms = tuple(Reminder("minutes", value) for value in range(10, 0, -1))
        page.field = EditorField.ALARMS
        page.key("end", "")
        _, lines = self.draw(page, self.config.popups.calendar.panel, 136, 44)
        self.assertIn("1 minutes before", "\n".join(lines))
        self.assertNotIn("10 minutes before", "\n".join(lines))
        self.assertEqual(len(page.draft.alarms), 10)
        self.assertEqual(sum("minutes before" in line and " Delete " in line for line in lines), 3)

    def test_reminder_wheel_is_scoped_to_three_row_list_without_clicking_first(self):
        page = self.calendar()
        page.draft.alarms = tuple(Reminder("minutes", value) for value in range(10, 0, -1))
        panel = self.config.popups.calendar.panel
        _, lines = self.draw(page, panel, 136, 44)
        y = next(i for i, line in enumerate(lines) if "10 minutes before" in line)
        x = lines[y].index("10 minutes before")  # PTK supplies character indices.
        self.assertEqual(page.field, EditorField.TITLE)
        self.surface.control.mouse_handler(MouseEvent(Point(x, y), MouseEventType.SCROLL_DOWN,
                                                     MouseButton.NONE, frozenset()))
        _, scrolled = self.draw(page, panel, 136, 44)
        self.assertEqual(page.field, EditorField.ALARMS)
        self.assertIn("7 minutes before", "\n".join(scrolled))
        self.assertNotIn("10 minutes before", "\n".join(scrolled))
        self.assertEqual(sum("minutes before" in line and " Delete " in line for line in scrolled), 3)
        offset = page._alarm_offset
        self.surface.control.mouse_handler(MouseEvent(Point(1, y), MouseEventType.SCROLL_DOWN,
                                                     MouseButton.NONE, frozenset()))
        self.assertEqual(page._alarm_offset, offset)

    def test_calendar_redraw_reuses_queries_and_narrow_editor_skips_hidden_month(self):
        item = CalendarEvent("Original", datetime(2026,11,7,9), datetime(2026,11,7,10),
                             repeat=Recurrence("days", 1), timezone="UTC")
        runtime = SimpleNamespace(snapshot=CalendarSnapshot((item,)), error="")
        page = CalendarPage(lambda: self.clock, lambda: self.config.popups.calendar, runtime)
        panel = self.config.popups.calendar.panel
        _, first = self.draw(page, panel, 136, 44)
        with patch.object(CalendarEvent, "on_day", side_effect=AssertionError("Repeated recurrence query")):
            _, second = self.draw(page, panel, 136, 44)
        self.assertEqual(first, second)
        page.key("enter", "")
        page.key("<any>", " edit")
        runtime.snapshot = CalendarSnapshot((replace(item, title="Updated"),))
        with patch.object(CalendarEvent, "on_day", side_effect=AssertionError("Hidden month query")):
            _, narrow = self.draw(page, panel, 35, 44)
        self.assertIn("Original edit", "\n".join(narrow))
        _, wide = self.draw(page, panel, 136, 44)
        self.assertIn("Updated", "\n".join(wide))
        self.assertIn("Original edit", "\n".join(wide))  # Keep the unsaved draft.

    def test_interactive_surface_does_not_wrap_unused_plain_content(self):
        with patch.object(self.surface, "_wrapped", side_effect=AssertionError("Unused plain body")):
            self.draw(self.calendar(), self.config.popups.calendar.panel, 136, 44)

    def test_pages_and_dropdowns_stay_bounded_in_small_viewports(self):
        calendar = self.calendar()
        weather = WeatherPage(WeatherSnapshot, lambda: self.config.widgets.weather, lambda: self.clock)
        for page, panel in ((calendar, self.config.popups.calendar.panel), (weather, self.config.popups.weather.panel)):
            for columns, rows in ((1,1), (4,3), (12,5), (35,12), (80,20)):
                with self.subTest(page=type(page).__name__, columns=columns, rows=rows):
                    _, lines = self.draw(page, panel, columns, rows)
                    self.assertLessEqual(len(lines), rows)
                    self.assertTrue(all(width(line) <= columns for line in lines))
        calendar.field = EditorField.REPEAT
        calendar.key("enter", "")
        _, lines = self.draw(calendar, self.config.popups.calendar.panel, 35,12)
        self.assertIn("List", lines[-2])
        self.assertTrue(all(width(line) <= 35 for line in lines))
