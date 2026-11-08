"""Registered offsets and deadline-based scheduling, including storage upgrades."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dok.features.calendar.config import CalendarConfig
from dok.features.calendar.editor import EditorField, EventDraft
from dok.features.calendar.models import CalendarEvent
from dok.features.calendar.reminders import Reminder, ReminderUnit
from dok.features.calendar.runtime import CalendarRuntime
from dok.features.calendar.store import CalendarStore


def meeting(**changes):
    values = dict(title="Meeting", start=datetime(2026, 11, 10, 9),
                  end=datetime(2026, 11, 10, 10), timezone="UTC")
    values.update(changes)
    return CalendarEvent(**values)


class ReminderEditorTests(unittest.TestCase):
    def test_start_ignores_number_and_removes_it_from_editable_focus(self):
        draft = EventDraft(date(2026, 11, 10), "UTC")
        draft.alarm_number.text = "not a number"
        draft.alarm_unit = ReminderUnit.START
        self.assertNotIn(EditorField.ALARM_NUMBER, draft.fields())
        self.assertFalse(draft.edit(EditorField.ALARM_NUMBER, "<any>", "x"))
        draft.add_alarm()
        self.assertEqual(draft.alarms, (Reminder("start", 0),))
        self.assertEqual(draft.alarm_number.text, "not a number")
        draft.alarm_unit = ReminderUnit.SECONDS
        with self.assertRaises(ValueError):
            draft.add_alarm()

    def test_register_deduplicates_equivalent_units_and_remove_preserves_other_offsets(self):
        draft = EventDraft(date(2026, 11, 10), "UTC")
        for unit, value in (("weeks", "1"), ("days", "7"), ("minutes", "10"), ("seconds", "600")):
            draft.alarm_unit = ReminderUnit(unit)
            draft.alarm_number.text = value
            draft.add_alarm()
        self.assertEqual(len(draft.alarms), 2)
        self.assertEqual([a.identity for a in draft.alarms], [604800000000, 600000000])
        draft.remove_alarm(0)
        self.assertEqual(draft.alarms, (Reminder("minutes", 10),))
        self.assertEqual(draft.alarm_index, 0)

    def test_start_changes_move_registered_offsets_and_support_seconds(self):
        original = meeting(alarms=(Reminder("seconds", 2.5), Reminder("start", 0)))
        draft = EventDraft(original.start.date(), "UTC", original)
        draft.buffer(EditorField.START_TIME).text = "09:15"
        changed = draft.event()
        self.assertEqual(changed.alarms, original.alarms)
        self.assertEqual(changed.latest_due(datetime(2026, 11, 10, 9, 15, tzinfo=timezone.utc),
                                           changed.alarms[0]).at,
                         datetime(2026, 11, 10, 9, 14, 57, 500000, tzinfo=timezone.utc))

    def test_dst_gap_skips_only_affected_offset(self):
        item = meeting(start=datetime(2026, 3, 7, 3, 30), end=datetime(2026, 3, 7, 4),
                       timezone="America/New_York", repeat={"unit": "days"},
                       alarms=(Reminder("hours", 1), Reminder("start", 0)))
        now = datetime(2026, 3, 8, 5, tzinfo=timezone.utc)
        self.assertEqual(item.next_due(now, Reminder("start", 0)).astimezone(timezone.utc),
                         datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc))
        self.assertEqual(item.next_due(now, Reminder("hours", 1)).date(), date(2026, 3, 9))

    def test_expired_long_event_stops_search_at_representable_end(self):
        item = meeting(start=datetime(2026, 1, 1), end=datetime(9999, 12, 31),
                       repeat={"unit": "days"}, alarms=(Reminder("start", 0),))
        with patch.object(CalendarEvent, "occurrence", wraps=item.occurrence) as occurrence:
            self.assertIsNone(item.next_due(datetime(2026, 1, 3, tzinfo=timezone.utc), item.alarms[0]))
            self.assertEqual(occurrence.call_count, 0)

    def test_clock_in_repeated_dst_hour_uses_instant_for_deadlines(self):
        item = meeting(start=datetime(2026, 10, 31, 1, 45), end=datetime(2026, 10, 31, 2),
                       timezone="America/New_York", repeat={"unit": "days"}, alarms=(Reminder("start", 0),))
        now = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1)
        self.assertEqual(item.latest_due(now, item.alarms[0]).at.astimezone(timezone.utc),
                         datetime(2026, 11, 1, 5, 45, tzinfo=timezone.utc))
        self.assertEqual(item.next_due(now, item.alarms[0]).date(), date(2026, 11, 2))


class ReminderStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = CalendarStore(Path(self.directory.name))

    def test_each_registered_alarm_is_claimed_once_and_next_deadline_is_exact(self):
        self.store.save(meeting(alarms=(Reminder("days", 1), Reminder("seconds", 10), Reminder("start", 0))))
        first = self.store.check(datetime(2026, 11, 9, 9, tzinfo=timezone.utc))
        self.assertEqual(len(first.due), 1)
        self.assertEqual(first.next_at, datetime(2026, 11, 10, 8, 59, 50, tzinfo=timezone.utc))
        other = CalendarStore(self.store.path.parent)
        self.assertFalse(other.check(datetime(2026, 11, 9, 10, tzinfo=timezone.utc)).due)
        self.assertEqual(len(other.check(datetime(2026, 11, 10, 9, tzinfo=timezone.utc)).due), 2)
        self.assertIsNone(other.check(datetime(2026, 11, 10, 10, tzinfo=timezone.utc)).next_at)

    def test_unchanged_watch_reuses_plan_but_external_edit_rebuilds_it(self):
        _, saved = self.store.save(meeting(alarms=(Reminder("start", 0),)))
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        original, calls = CalendarEvent.next_due, []
        def next_due(event, current, alarm):
            calls.append(True)
            return original(event, current, alarm)
        with patch.object(CalendarEvent, "next_due", next_due):
            self.store.check(now)
            for second in range(1, 5):
                self.store.check(now + timedelta(seconds=second))
            self.assertEqual(len(calls), 1)
            CalendarStore(self.store.path.parent).save(replace(saved, alarms=(Reminder("days", 1),)))
            self.assertEqual(self.store.check(now + timedelta(seconds=5)).next_at,
                             datetime(2026, 11, 9, 9, tzinfo=timezone.utc))
            self.assertEqual(len(calls), 2)

    def test_single_alarm_file_preserves_receipt_and_changes_format_only_on_write(self):
        old = meeting(id="old", revision=2).to_dict()
        old.pop("alarms")
        old["alarm"] = "2026-11-10T08:50:00"
        document = {"storage_version": 1, "events": [old], "reminders": {"old:2": old["alarm"]}}
        self.store.path.write_text(json.dumps(document))
        before = self.store.path.read_bytes()
        loaded = self.store.load().events[0]
        self.assertEqual(loaded.alarms, (Reminder("minutes", 10),))
        self.assertFalse(self.store.check(datetime(2026, 11, 10, 9, tzinfo=timezone.utc)).due)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.save(replace(loaded, title="Updated"))
        updated = json.loads(self.store.path.read_text())
        self.assertEqual(updated["storage_version"], CalendarStore.VERSION)
        self.assertNotIn("alarm", updated["events"][0])


class ReminderDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_and_close_or_disable_cannot_leave_scheduler_running(self):
        for close in (True, False):
            with self.subTest(close=close), tempfile.TemporaryDirectory() as directory:
                runtime = CalendarRuntime(Path(directory), lambda: None, lambda *a, **k: None)
                clock = SimpleNamespace(now=lambda: datetime.now(timezone.utc))
                config = CalendarConfig(idle_s=.01)
                class StopAtTimeout(asyncio.Event):
                    async def wait(self):
                        try:
                            return await super().wait()
                        except asyncio.CancelledError:
                            if close:
                                runtime.close()
                            else:
                                runtime.sync(replace(config, enabled=False), lambda: clock, lambda: True)
                            raise
                runtime._wake = StopAtTimeout()
                runtime.sync(config, lambda: clock, lambda: True)
                task = runtime._slot._task
                try:
                    await asyncio.wait_for(asyncio.shield(task), 1)
                    self.assertTrue(task.done())
                finally:
                    runtime.close()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_empty_closed_calendar_does_not_read_each_clock_tick_and_save_wakes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            delivered = asyncio.Event()
            runtime = CalendarRuntime(Path(directory), lambda: None, lambda *a, **k: delivered.set())
            clock = SimpleNamespace(now=lambda: datetime.now(timezone.utc))
            config = CalendarConfig(idle_s=30, clock_s=.01)
            with patch.object(runtime.store, "check", wraps=runtime.store.check) as check:
                runtime.sync(config, lambda: clock, lambda: True)
                try:
                    async with asyncio.timeout(2):
                        while not check.call_count:
                            await asyncio.sleep(.005)
                    await asyncio.sleep(.06)
                    self.assertEqual(check.call_count, 1)
                    start = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=.15)
                    await runtime.save(meeting(start=start, end=start + timedelta(hours=1),
                                               alarms=(Reminder("start", 0),)))
                    await asyncio.wait_for(delivered.wait(), 2)
                    self.assertLess(check.call_count, 6)
                finally:
                    runtime.close()
                    await asyncio.gather(runtime._slot._task, return_exceptions=True)

    async def test_clock_jump_delivers_without_waiting_for_external_file_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            delivered = asyncio.Event()
            runtime = CalendarRuntime(Path(directory), lambda: None, lambda *a, **k: delivered.set())
            now = datetime(2026, 11, 8, tzinfo=timezone.utc)
            clock = SimpleNamespace(now=lambda: now)
            await runtime.save(meeting(alarms=(Reminder("start", 0),)))
            runtime.sync(CalendarConfig(idle_s=30, clock_s=.01), lambda: clock, lambda: True)
            try:
                await asyncio.sleep(.04)
                now = datetime(2026, 11, 10, 10, tzinfo=timezone.utc)
                await asyncio.wait_for(delivered.wait(), 2)
            finally:
                runtime.close()
                await asyncio.gather(runtime._slot._task, return_exceptions=True)
