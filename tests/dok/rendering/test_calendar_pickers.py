"""Picker focus, mouse routing, approved centering and viewport bounds."""
from datetime import date, datetime, time
from types import SimpleNamespace
import unittest

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from dok import DockConfig
from dok.features.calendar.editor import EditorField
from dok.features.calendar.models import CalendarEvent
from dok.features.calendar.picker_view import DateFocus
from dok.features.calendar.selection import TimePart
from dok.features.calendar.recurrence import RepeatEnd, RepeatUnit
from dok.features.calendar.store import CalendarSnapshot
from dok.features.calendar.view import CalendarPage
from dok.locales import Locale
from dok.popups.config import PopupRect
from dok.rendering.popups import PopupSurface
from dok.rendering.text import width
from prompt_toolkit.styles import Style


class CalendarPickerTests(unittest.TestCase):
    def setUp(self):
        self.config = DockConfig.initial()
        self.clock = SimpleNamespace(now=lambda: datetime(2026, 11, 8), timezone="UTC")
        self.original = CalendarEvent("Meeting", datetime(2026, 11, 10, 9, 30), datetime(2026, 11, 10, 10, 30), timezone="UTC")
        runtime = SimpleNamespace(snapshot=CalendarSnapshot((self.original,)), error="")
        self.page = CalendarPage(lambda: self.clock, lambda: self.config.popups.calendar, runtime)
        self.page._select(self.original.start.date())
        self.page.key("enter", "")

    def open(self, field):
        self.page.field = field
        self.page.key("enter", "")

    def draw(self, columns=134, rows=39):
        return self.page.draw_body(columns, rows, self.config.theme)

    @staticmethod
    def click(item):
        item[2](MouseEvent(Point(0, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))

    def test_date_cancel_preserves_draft_and_apply_updates_only_chosen_field(self):
        before = self.page.draft.signature
        self.open(EditorField.START_DATE)
        self.page.key("right", "")
        self.assertEqual(self.page.draft.signature, before)
        self.page.key("escape", "")
        self.assertIsNone(self.page._picker)
        self.assertTrue(self.page.editing)
        self.assertEqual(self.page.draft.signature, before)
        self.open(EditorField.END_DATE)
        self.page.key("pagedown", "")
        self.page.key("right", "")
        self.page.key("enter", "")
        self.assertIsNone(self.page._picker)
        self.assertEqual(self.page.draft.temporal_value(EditorField.END_DATE), date(2026, 12, 11))
        self.assertEqual(self.page.draft.temporal_value(EditorField.START_DATE), self.original.start.date())

    def test_schedule_sections_limit_order_padding_and_list_delete_background(self):
        self.page._locale = lambda: Locale.resolve("ko")
        draft = self.page.draft
        draft.unit = RepeatUnit.WEEKS
        draft.buffer(EditorField.REPEAT_COUNT).text = "3"
        draft.add_alarm()
        body = self.draw()
        rows = self.page._field_rows
        order = [EditorField.TITLE, EditorField.START_DATE, EditorField.INTERVAL,
                 EditorField.REPEAT_COUNT, EditorField.ALARM_NUMBER, EditorField.COMMAND, EditorField.NOTES]
        self.assertEqual([rows[field] for field in order], sorted(rows[field] for field in order))
        right = self.page._field_columns[EditorField.REPEAT_END]
        self.assertGreater(right, 0)
        line = "".join(item[1] for item in body[rows[EditorField.REPEAT_COUNT]])
        self.assertLess(line.index("3회"), line.index("반복"))
        pane = lambda row: "".join(item[1] for item in row).split("│")[-1].strip()
        self.assertEqual(pane(body[rows[EditorField.REPEAT_COUNT] - 1]), "")
        self.assertNotIn("최초 일정", "\n".join("".join(item[1] for item in row) for row in body))
        delete = next(item for item in body[rows[EditorField.ALARMS]] if item[1].strip() == "삭제")
        self.assertEqual(Style([]).get_attrs_for_style_str(delete[0]).bgcolor,
                         self.config.theme.resolve("input_bg")[1:])
        draft.buffer(EditorField.REPEAT_COUNT).text = "0"
        self.assertIn("계속", "".join(item[1] for row in self.draw() for item in row))

    def test_repeat_end_date_uses_same_picker_and_preserves_inactive_count(self):
        draft = self.page.draft
        draft.unit = RepeatUnit.WEEKS
        draft.buffer(EditorField.REPEAT_COUNT).text = "3"
        self.open(EditorField.REPEAT_END)
        self.page.key("down", "")
        self.page.key("enter", "")
        self.assertEqual(draft.repeat_end, RepeatEnd.DATE)
        self.open(EditorField.REPEAT_UNTIL)
        self.page.key("pagedown", "")
        self.page.key("enter", "")
        self.assertEqual(draft.event().repeat.until, date(2026, 12, 10))
        self.assertEqual(draft.buffers[EditorField.REPEAT_COUNT].text, "3")

    def test_year_month_focus_and_enter_apply_do_not_require_typing(self):
        self.open(EditorField.END_DATE)
        self.page.key("s-tab", "")  # Date -> month.
        self.page.key("s-tab", "")  # Month -> year.
        self.assertEqual(self.page._picker.focus, DateFocus.YEAR)
        self.page.key("right", "")
        self.page.key("enter", "")
        self.assertEqual(self.page.draft.temporal_value(EditorField.END_DATE), date(2027, 11, 10))

    def test_time_keyboard_selection_is_pending_until_apply_and_can_cancel(self):
        self.open(EditorField.START_TIME)
        self.page.key("tab", "")
        self.page.key("down", "")
        self.page.key("right", "")
        self.page.key("pagedown", "")
        self.assertEqual(self.page.draft.temporal_value(EditorField.START_TIME), time(9, 30))
        self.page.key("enter", "")
        self.assertEqual(self.page.draft.temporal_value(EditorField.START_TIME), time(9, 31, 5))
        self.open(EditorField.START_TIME)
        self.page.key("home", "")
        self.page.key("escape", "")
        self.assertEqual(self.page.draft.temporal_value(EditorField.START_TIME), time(9, 31, 5))

    def test_date_click_applies_and_outside_click_only_cancels(self):
        self.open(EditorField.END_DATE)
        body = self.draw()
        rect = self.page._picker_rect
        items = [item for row in body[rect.top:rect.top + rect.height] for item in row
                 if item[1] == "11" and len(item) == 3]
        self.assertEqual(len(items), 1)
        self.click(items[0])
        self.assertIsNone(self.page._picker)
        self.assertEqual(self.page.draft.temporal_value(EditorField.END_DATE), date(2026, 11, 11))
        before = self.page.draft.signature
        self.open(EditorField.START_DATE)
        self.page.key("right", "")
        body = self.draw()
        self.click(next(item for item in body[0] if len(item) == 3))
        self.assertIsNone(self.page._picker)
        self.assertEqual(self.page.draft.signature, before)
        self.assertTrue(self.page.editing)

    def test_local_wheel_changes_only_target_time_column_and_keeps_editor_still(self):
        self.open(EditorField.START_TIME)
        self.draw()
        rect = self.page._picker_rect
        self.assertTrue(self.page.scroll_at(rect.left + rect.width // 2, rect.top + 2, 3))
        self.assertEqual(self.page._picker.selection.value, time(9, 33))
        self.assertEqual(self.page._picker.focus, TimePart.MINUTE)
        self.assertTrue(self.page.scroll_at(0, 0, 3))
        self.assertEqual(self.page._picker.selection.value, time(9, 33))
        self.assertEqual(self.page._scroll, 0)
        self.assertFalse(self.page.draft.dirty)

    def test_korean_surface_wheel_uses_cells_including_picker_border(self):
        self.page._locale = lambda: Locale.resolve("ko")
        self.open(EditorField.START_TIME)
        surface = PopupSurface(lambda: None, lambda: None, modal=True)
        surface.page = self.page
        surface.draw(self.page.content(), self.config.popups.calendar.panel, PopupRect(0, 0, 136, 44), theme=self.config.theme)
        rect = self.page._picker_rect
        cell_x = surface._body_origin[0] + rect.left + rect.width // 2
        y = surface._body_origin[1] + rect.top + 1  # Korean column labels.
        text = "".join(item[1] for item in surface._painted_rows[y])
        char_x = next(index for index in range(len(text) + 1) if width(text[:index]) >= cell_x)
        surface.control.mouse_handler(MouseEvent(Point(char_x, y), MouseEventType.SCROLL_DOWN, MouseButton.NONE, frozenset()))
        self.assertEqual(self.page._picker.selection.value, time(9, 33))
        self.assertIn("시각", self.page.status_text())

    def test_closed_buttons_are_equal_width_centered_with_three_cell_gap(self):
        for columns in (100, 134, 137, 140):
            with self.subTest(columns=columns):
                body = self.draw(columns)
                for start, end in ((EditorField.START_DATE, EditorField.END_DATE), (EditorField.START_TIME, EditorField.END_TIME)):
                    row = body[self.page._field_rows[start]]
                    positions = []
                    x = 0
                    for item in row:
                        if "" in item[1]:
                            text = item[1]
                            positions.append((x, width(text)))
                            self.assertEqual(len(text) - len(text.lstrip(" ")), len(text) - len(text.rstrip(" ")))
                        x += width(item[1])
                    self.assertEqual(len(positions), 2)
                    self.assertEqual(positions[0][1], positions[1][1])
                    self.assertEqual(positions[1][0] - sum(positions[0]), 3)

    def test_picker_resizes_without_losing_pending_value_or_exceeding_viewport(self):
        for field in (EditorField.START_DATE, EditorField.START_TIME):
            self.open(field)
            self.page.key("down", "")
            pending = self.page._picker.selection.value
            for columns, rows in ((134, 39), (68, 20), (35, 8), (12, 4), (2, 2), (1, 1)):
                with self.subTest(field=field, columns=columns, rows=rows):
                    body = self.draw(columns, rows)
                    self.assertLessEqual(len(body), rows)
                    self.assertTrue(all(sum(width(item[1]) for item in row) <= columns for row in body))
                    self.assertEqual(self.page._picker.selection.value, pending)
            self.page.key("escape", "")
