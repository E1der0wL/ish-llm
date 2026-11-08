"""Finite repetition, event defaults and durable command admission."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dok.features.calendar.commands import CommandResult
from dok.features.calendar.config import CalendarConfig
from dok.features.calendar.editor import EditorField, EventDraft
from dok.features.calendar.models import CalendarEvent
from dok.features.calendar.recurrence import Recurrence, RepeatEnd, RepeatUnit
from dok.features.calendar.reminders import Reminder
from dok.features.calendar.runtime import CalendarRuntime
from dok.features.calendar.store import CalendarStore
from dok.locales import Locale


def meeting(**changes):
    values = dict(title="Scheduled", start=datetime(2026, 11, 10, 9), end=datetime(2026, 11, 10, 10),
                  timezone="UTC", command="printf scheduled")
    values.update(changes)
    return CalendarEvent(**values)


class ScheduleModelTests(unittest.TestCase):
    def test_count_display_keeps_numeric_storage_and_cursor_before_suffix(self):
        locale = Locale.resolve("ko")
        self.assertEqual(Recurrence.count_display("0", 1, locale), ("계속", 2))
        self.assertEqual(Recurrence.count_display("3", 1, locale), ("3회", 1))
        self.assertEqual(Recurrence.count_display("003", 2, locale), ("3회", 0))
        self.assertEqual(Recurrence.count_display("", 0, locale), ("", 0))
        self.assertEqual(Recurrence.count_display("-", 1, locale), ("-", 1))

    def test_new_times_start_at_next_whole_hour_with_date_rollover(self):
        draft = EventDraft(date(2026, 11, 8), "UTC", now=datetime(2026, 11, 8, 23, 30, 20, 123))
        self.assertEqual(draft.temporal_value(EditorField.START_DATE), date(2026, 11, 9))
        self.assertEqual(draft.temporal_value(EditorField.START_TIME), time(0))
        self.assertEqual(draft.temporal_value(EditorField.END_DATE), date(2026, 11, 9))
        self.assertEqual(draft.temporal_value(EditorField.END_TIME), time(1))
        self.assertEqual(draft.alarm_number.text, "5")
        self.assertEqual(Reminder().delta, timedelta(minutes=5))

    def test_initial_times_use_selected_day_and_keep_whole_hours_at_date_limit(self):
        cases = ((date(2026, 11, 20), 14, time(15), time(16)),
                 (date.max, 23, time(22), time(23)))
        for day, hour, start, end in cases:
            with self.subTest(day=day, hour=hour):
                draft = EventDraft(day, "UTC", now=datetime(2026, 11, 8, hour, 37, 20, 123))
                self.assertEqual(draft.temporal_value(EditorField.START_DATE), day)
                self.assertEqual(draft.temporal_value(EditorField.START_TIME), start)
                self.assertEqual(draft.temporal_value(EditorField.END_DATE), day)
                self.assertEqual(draft.temporal_value(EditorField.END_TIME), end)

    def test_start_selections_never_reset_end_defaults_or_explicit_values(self):
        draft = EventDraft(date(2026, 11, 8), "UTC", now=datetime(2026, 11, 8, 23, 30))
        draft.choose_temporal(EditorField.START_TIME, time(2))
        self.assertEqual(draft.temporal_value(EditorField.END_TIME), time(1))
        draft.choose_temporal(EditorField.START_DATE, date(2026, 11, 10))
        self.assertEqual(draft.temporal_value(EditorField.END_DATE), date(2026, 11, 9))
        draft.choose_temporal(EditorField.END_TIME, time(5))
        draft.choose_temporal(EditorField.START_TIME, time(3))
        self.assertEqual(draft.temporal_value(EditorField.END_TIME), time(5))
        draft.toggle_all_day()
        draft.choose_temporal(EditorField.START_DATE, date(2026, 11, 11))
        self.assertEqual(draft.temporal_value(EditorField.END_DATE), date(2026, 11, 9))

    def test_existing_times_and_location_are_preserved_without_becoming_commands(self):
        original = meeting(command="", location="Old meeting room",
                           start=datetime(2026, 11, 10, 9, 12, 34, 567),
                           end=datetime(2026, 11, 10, 10, 23, 45, 678))
        draft = EventDraft(original.start.date(), "Asia/Seoul", original, now=datetime(2030, 1, 1, 15))
        self.assertEqual(draft.event(), original)
        self.assertEqual(draft.buffer(EditorField.COMMAND).text, "")
        draft.choose_temporal(EditorField.START_TIME, time(8))
        self.assertEqual(draft.temporal_value(EditorField.END_TIME), original.end.time())
        self.assertEqual(draft.event().location, original.location)

    def test_count_includes_first_and_limits_calendar_reminders_and_commands(self):
        event = meeting(repeat=Recurrence("weeks", 1, count=3), alarms=(Reminder(),))
        self.assertEqual([event.occurrence(index).start.date() for index in range(3)],
                         [date(2026, 11, 10), date(2026, 11, 17), date(2026, 11, 24)])
        self.assertIsNone(event.occurrence(3))
        self.assertFalse(tuple(event.on_day(date(2026, 12, 1))))
        after = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(event.latest_due(after, Reminder()).event.start.date(), date(2026, 11, 24))
        self.assertIsNone(event.next_due(after, Reminder()))
        forever = replace(event, repeat=Recurrence("weeks", 1, count=0))
        self.assertIsNotNone(forever.occurrence(100))

    def test_end_date_includes_last_start_and_overnight_event_tail(self):
        event = meeting(start=datetime(2026, 11, 10, 23), end=datetime(2026, 11, 11, 1),
                        repeat=Recurrence("days", 1, RepeatEnd.DATE, until=date(2026, 11, 12)))
        self.assertIsNotNone(event.occurrence(2))
        self.assertIsNone(event.occurrence(3))
        self.assertEqual(len(tuple(event.on_day(date(2026, 11, 13)))), 1)
        self.assertFalse(tuple(event.on_day(date(2026, 11, 14))))
        self.assertEqual(CalendarEvent.from_dict(event.to_dict()), event)

    def test_monthly_limits_clamp_without_creating_an_extra_occurrence(self):
        event = meeting(start=datetime(2024, 1, 31, 9), end=datetime(2024, 1, 31, 10),
                        repeat=Recurrence("months", 1, RepeatEnd.DATE, until=date(2024, 3, 30)))
        self.assertEqual(event.occurrence(1).start.date(), date(2024, 2, 29))
        self.assertIsNone(event.occurrence(2))
        self.assertEqual(event.repeat.index_near(event.start.date(), date.max), 1)
        for changes in ({"count": -1}, {"end": "date"}, {"end": "count", "until": date(2026, 1, 1)}):
            with self.assertRaises(ValueError):
                Recurrence("days", **changes)
        with self.assertRaises(ValueError):
            replace(event, repeat=Recurrence("days", 1, "date", until=date(2024, 1, 30)))

    def test_inactive_repeat_values_do_not_validate_or_enter_saved_rule(self):
        draft = EventDraft(date(2026, 11, 8), "UTC", now=datetime(2026, 11, 8, 14))
        draft.buffer(EditorField.TITLE).text = "Example"
        draft.buffers[EditorField.REPEAT_COUNT].text = "invalid"
        self.assertEqual(draft.event().repeat, Recurrence())
        self.assertNotIn(EditorField.REPEAT_END, draft.fields())
        draft.unit, draft.repeat_end = RepeatUnit.WEEKS, RepeatEnd.DATE
        draft.choose_temporal(EditorField.REPEAT_UNTIL, date(2026, 11, 30))
        self.assertNotIn(EditorField.REPEAT_COUNT, draft.fields())
        self.assertEqual(draft.event().repeat.until, date(2026, 11, 30))


class CommandStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = CalendarStore(self.directory)
        self.before = datetime(2026, 11, 10, 8, tzinfo=timezone.utc)
        self.due = self.before + timedelta(hours=1)

    def test_command_claim_is_independent_of_reminders_and_unique_across_instances(self):
        self.store.save(meeting())
        other = CalendarStore(self.directory)
        for store in (self.store, other):
            armed = store.check(self.before, False, True)
            self.assertEqual(armed.next_at, self.due)
            self.assertFalse(armed.commands)
        check = self.store.check(self.due, False, True)
        self.assertFalse(check.due)
        self.assertEqual(len(check.commands), 1)
        self.assertFalse(other.check(self.due, False, True).commands)
        self.assertFalse(self.store.check(self.before, False, True).commands)
        self.assertFalse(self.store.check(self.due, False, True).commands)

    def test_startup_or_newly_seen_past_command_is_not_replayed(self):
        _, saved = self.store.save(meeting())
        self.assertFalse(self.store.check(self.due + timedelta(seconds=1), False, True).commands)
        self.assertFalse(CalendarStore(self.directory).check(self.due + timedelta(hours=1), False, True).commands)
        self.store.save(replace(saved, command="printf updated"))
        self.assertFalse(self.store.check(self.due + timedelta(hours=2), False, True).commands)

    def test_finite_command_repetition_and_deletion_cancel_deadlines(self):
        _, saved = self.store.save(meeting(repeat=Recurrence("days", 1, count=2)))
        self.store.check(self.before, False, True)
        first = self.store.check(self.due, False, True)
        self.assertEqual(len(first.commands), 1)
        self.assertEqual(first.next_at, self.due + timedelta(days=1))
        last = self.store.check(first.next_at, False, True)
        self.assertEqual(len(last.commands), 1)
        self.assertIsNone(last.next_at)
        self.assertFalse(self.store.check(self.due + timedelta(days=20), False, True).commands)
        _, saved = self.store.save(replace(saved, repeat=Recurrence("days")))
        self.store.check(self.before, False, True)
        self.store.delete(saved)
        self.assertIsNone(self.store.check(self.before, False, True).next_at)

    def test_failed_atomic_claim_does_not_poison_receipts(self):
        self.store.save(meeting())
        self.store.check(self.before, False, True)
        with patch("dok.features.calendar.store.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.store.check(self.due, False, True)
        self.assertEqual(len(self.store.check(self.due, False, True).commands), 1)
        self.assertFalse(self.store.check(self.due, False, True).commands)

    def test_version_two_data_is_read_without_writing_or_turning_location_into_command(self):
        event = meeting(command="", location="Room A").to_dict()
        del event["command"]
        event["repeat"] = {"unit": "none", "interval": 1}
        payload = json.dumps({"storage_version": 2, "events": [event], "reminders": {}})
        self.store.path.write_text(payload)
        loaded = self.store.load().events[0]
        self.assertEqual(loaded.location, "Room A")
        self.assertEqual(loaded.command, "")
        self.assertEqual(self.store.path.read_text(), payload)
        self.store.save(loaded)
        self.assertEqual(json.loads(self.store.path.read_text())[CalendarStore.VERSION_FIELD], CalendarStore.VERSION)


class CommandRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_skip_notifies_once_and_submitted_command_does_not_notify(self):
        for outcome in (CommandResult.SKIPPED, CommandResult.SUBMITTED, CommandResult.FAILED):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                calls, notices = [], []
                def submit(command):
                    calls.append(command)
                    return outcome
                runtime = CalendarRuntime(Path(directory), lambda: None,
                                          lambda *args, **kwargs: notices.append(args), submit=submit)
                now = datetime(2026, 11, 10, 8, tzinfo=timezone.utc)
                clock = SimpleNamespace(now=lambda: now)
                await runtime.save(meeting())
                observed = threading.Event()
                check = runtime.store.check
                def checked(*args):
                    result = check(*args)
                    observed.set()
                    return result
                runtime.store.check = checked
                runtime.sync(CalendarConfig(idle_s=30, clock_s=.01), lambda: clock, lambda: True)
                try:
                    async with asyncio.timeout(2):
                        while not observed.is_set():
                            await asyncio.sleep(.005)
                    now += timedelta(hours=1)
                    async with asyncio.timeout(2):
                        while not calls:
                            await asyncio.sleep(.005)
                    await asyncio.sleep(.03)
                    self.assertEqual(calls, ["printf scheduled"])
                    self.assertEqual(len(notices), int(outcome != CommandResult.SUBMITTED))
                finally:
                    runtime.close()
                    await asyncio.gather(runtime._slot._task, return_exceptions=True)
