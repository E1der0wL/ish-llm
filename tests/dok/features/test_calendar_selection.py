"""Date/time selection rules and their application to a local event draft."""
from datetime import date, datetime, time, timezone
import unittest

from dok.features.calendar.editor import EditorField, EventDraft
from dok.features.calendar.models import CalendarEvent
from dok.features.calendar.selection import DateSelection, TimePart, TimeSelection


class SelectionRulesTests(unittest.TestCase):
    def test_month_year_navigation_clamps_days_and_respects_date_boundaries(self):
        state = DateSelection(date(2024, 1, 31), 6)
        state.months(1)
        self.assertEqual(state.value, date(2024, 2, 29))
        state.years(1)
        self.assertEqual(state.value, date(2025, 2, 28))
        state.select(date.min)
        state.days(-1)
        state.months(-1)
        self.assertEqual(state.value, date.min)
        state.select(date.max)
        state.days(1)
        state.years(1)
        self.assertEqual(state.value, date.max)

    def test_month_grid_uses_week_start_without_overflow_at_python_boundaries(self):
        for day in (date.min, date(2024, 2, 29), date.max):
            for first in (0, 6):
                with self.subTest(day=day, first=first):
                    state = DateSelection(day, first)
                    self.assertEqual(state.weekdays[0], first)
                    flattened = [item for week in state.weeks for item in week if item]
                    self.assertEqual(flattened[0], day.replace(day=1))
                    self.assertIn(day, flattened)
                    self.assertEqual(len(flattened), len(set(flattened)))
                    state.last_day()
                    self.assertEqual(state.value, flattened[-1])

    def test_time_parts_clamp_without_changing_other_parts_or_existing_fraction(self):
        state = TimeSelection(time(12, 30, 20, 123456))
        for part in TimePart:
            before = state.value
            state.step(part, 1000)
            self.assertEqual(state.current(part), part.maximum)
            self.assertEqual(state.value.microsecond, 123456)
            for other in TimePart:
                if other != part:
                    self.assertEqual(state.current(other), getattr(before, other.value))
            state.step(part, -1000)
            self.assertEqual(state.current(part), part.minimum)
            with self.assertRaises(ValueError):
                state.select(part, part.maximum + 1)

    def test_time_choices_scroll_at_both_ends_and_include_selected_value(self):
        for part in TimePart:
            state = TimeSelection(time())
            self.assertEqual(list(state.window(part, 5)), list(range(5)))
            state.select(part, part.maximum)
            self.assertEqual(list(state.window(part, 5)), list(range(part.maximum - 4, part.maximum + 1)))
            self.assertEqual(len(state.window(part, 1000)), part.maximum + 1)

    def test_typed_draft_api_preserves_clean_values_and_rejects_wrong_types(self):
        original = CalendarEvent("Meeting", datetime(2026, 11, 10, 9, 30), datetime(2026, 11, 10, 10, 30), timezone="UTC")
        draft = EventDraft(original.start.date(), "UTC", original)
        for field in EditorField:
            if field.temporal_type is not None:
                draft.choose_temporal(field, draft.temporal_value(field))
        self.assertFalse(draft.dirty)
        self.assertEqual(draft.temporal_label(EditorField.START_TIME), "09:30:00")
        for key, data in (("<any>", "1"), ("<bracketed-paste>", "bad date"), ("backspace", "")):
            self.assertFalse(draft.edit(EditorField.START_DATE, key, data))
        self.assertEqual(draft.event(), original)
        with self.assertRaises(ValueError):
            draft.choose_temporal(EditorField.START_DATE, time(9))
        with self.assertRaises(ValueError):
            draft.choose_temporal(EditorField.START_TIME, time(9, tzinfo=timezone.utc))

    def test_confirmed_values_keep_event_ordering_dst_and_all_day_validation(self):
        draft = EventDraft(date(2026, 3, 8), "America/New_York", now=datetime(2026, 3, 8, 8))
        draft.buffer(EditorField.TITLE).text = "Meeting"
        draft.choose_temporal(EditorField.END_TIME, time(10))
        draft.choose_temporal(EditorField.START_TIME, time(2, 30))
        with self.assertRaises(ValueError):
            draft.event()  # Spring-forward gap is still rejected by the event.
        draft.choose_temporal(EditorField.START_TIME, time(11))
        with self.assertRaises(ValueError):
            draft.event()  # End is before start.
        draft.choose_temporal(EditorField.END_TIME, time(12, 30, 45, 123456))
        self.assertEqual(draft.event().end, datetime(2026, 3, 8, 12, 30, 45, 123456))
        draft.toggle_all_day()
        draft.choose_temporal(EditorField.END_DATE, date(2026, 3, 10))
        before = draft.temporal_value(EditorField.END_TIME)
        draft.choose_temporal(EditorField.END_TIME, time(1))
        self.assertEqual(draft.temporal_value(EditorField.END_TIME), before)
        self.assertNotIn(EditorField.END_TIME, draft.fields())
        self.assertEqual(draft.event().end, datetime(2026, 3, 11))
