"""Calendar persistence, recurrence, conflicts and durable reminder admission."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dok.features.calendar.models import CalendarEvent, Recurrence
from dok.features.calendar.store import CalendarStore, CalendarConflict, CalendarSnapshot
from dok.features.calendar.runtime import CalendarRuntime
from dok.features.calendar.config import CalendarConfig
from dok.features.calendar.editor import EventDraft, EditorField


def event(**changes):
    return CalendarEvent(title="Meeting", start=datetime(2026, 1, 31, 9), end=datetime(2026, 1, 31, 10),
                         timezone="UTC", **changes)


class CalendarModelTests(unittest.TestCase):
    def test_month_end_and_leap_year_recur_from_original_anchor(self):
        monthly = event(repeat=Recurrence("months", 1))
        self.assertEqual([monthly.occurrence(i).start.date() for i in range(3)],
                         [date(2026,1,31), date(2026,2,28), date(2026,3,31)])
        rule = Recurrence("years", 1)
        self.assertEqual(rule.day(date(2024,2,29), 1), date(2025,2,28))
        self.assertEqual(rule.day(date(2024,2,29), 4), date(2028,2,29))
        self.assertIsNone(rule.day(date(9999,12,31), 1))

    def test_invalid_event_and_dst_gap_are_rejected(self):
        for changes in ({"end": datetime(2026,1,31,8)}, {"title": ""}, {"all_day": True}):
            with self.assertRaises(ValueError):
                replace(event(), **changes)
        with self.assertRaises(ValueError):
            CalendarEvent("DST", datetime(2026,3,8,2,30), datetime(2026,3,8,4), timezone="America/New_York")

    def test_repeat_due_is_timezone_aware_and_coalesces_without_replaying_history(self):
        original = event(alarm=datetime(2026,1,31,8,50), repeat=Recurrence("days", 1))
        occurrence = original.latest_due(datetime(2026,11,7,8,51,tzinfo=timezone.utc))
        self.assertEqual(occurrence.start, datetime(2026,11,7,9))
        self.assertEqual(occurrence.alarm, datetime(2026,11,7,8,50))
        self.assertEqual(original.latest_due(datetime(2026,11,7,8,49,tzinfo=timezone.utc)).start.day, 6)
        self.assertIsNone(original.latest_due(datetime(2025,1,1,tzinfo=timezone.utc)))

    def test_multiday_and_all_day_use_exclusive_end_and_one_entry_per_series(self):
        item = CalendarEvent("Holiday", datetime(2026,11,7), datetime(2026,11,9), all_day=True, holiday=True,
                             repeat=Recurrence("days", 1))
        self.assertEqual(len(tuple(item.on_day(date(2026,11,8)))), 1)
        one = replace(item, repeat=Recurrence())
        self.assertFalse(tuple(one.on_day(date(2026,11,9))))

    def test_record_roundtrip_preserves_alarm_and_owner_validation(self):
        item = event(alarm=datetime(2026,1,31,8,50), notes="one\ntwo", holiday=True)
        self.assertEqual(CalendarEvent.from_dict(item.to_dict()), item)
        with self.assertRaises(ValueError):
            Recurrence("days", 0)

    def test_draft_typing_paste_and_all_day_conversion(self):
        draft = EventDraft(date(2026,11,7), "Asia/Seoul")
        draft.edit(EditorField.TITLE, "<bracketed-paste>", "hello\nworld")
        draft.all_day = True
        saved = draft.event()
        self.assertEqual(saved.title, "hello world")
        self.assertEqual((saved.end - saved.start).days, 1)
        self.assertTrue(draft.dirty)
        draft = EventDraft(saved.start.date(), saved.timezone, saved)
        self.assertFalse(draft.dirty)

    def test_separate_date_time_fields_roundtrip_inclusive_all_day_dates(self):
        source = CalendarEvent("Days", datetime(2026,11,7), datetime(2026,11,10), all_day=True)
        draft = EventDraft(source.start.date(), "UTC", source)
        self.assertEqual(draft.buffer(EditorField.START_DATE).text, "2026-11-07")
        self.assertEqual(draft.buffer(EditorField.END_DATE).text, "2026-11-09")
        self.assertNotIn(EditorField.START_TIME, draft.fields())
        self.assertEqual(draft.event(), source)
        draft.toggle_all_day()
        self.assertIn(EditorField.START_TIME, draft.fields())
        draft.buffer(EditorField.START_TIME).text = "09:12:34.567890"
        draft.buffer(EditorField.END_TIME).text = "10:30"
        saved = draft.event()
        self.assertEqual(saved.start, datetime(2026,11,7,9,12,34,567890))
        self.assertEqual(saved.end, datetime(2026,11,9,10,30))

    def test_all_day_upper_date_boundary_is_a_validation_error(self):
        draft = EventDraft(date.max, "UTC")
        draft.buffer(EditorField.TITLE).text = "Boundary"
        draft.toggle_all_day()
        with self.assertRaises(ValueError):
            draft.event()


class CalendarQueryTests(unittest.TestCase):
    def test_queries_reuse_one_month_without_affecting_equality_or_new_snapshots(self):
        item = event(repeat=Recurrence("days", 1))
        snapshot = CalendarSnapshot((item,))
        original = CalendarEvent.on_day
        calls = []
        def on_day(event, day):
            calls.append(day)
            return original(event, day)
        with patch.object(CalendarEvent, "on_day", on_day):
            first = snapshot.on_day(date(2026, 11, 7))
            self.assertIs(snapshot.on_day(date(2026, 11, 7)), first)
            self.assertEqual(len(calls), 1)
            self.assertEqual(snapshot, CalendarSnapshot((item,)))
            snapshot.on_day(date(2026, 12, 1))
            snapshot.on_day(date(2026, 11, 7))
            self.assertEqual(len(calls), 3)  # Previous month's cache was released.
            changed = CalendarSnapshot((replace(item, title="Changed"),))
            self.assertEqual(changed.on_day(date(2026, 11, 7))[0].title, "Changed")
            self.assertEqual(first[0].title, "Meeting")

    def test_system_timezone_change_invalidates_cached_dst_interpretation(self):
        try:
            with patch.dict(os.environ, {"TZ": "UTC"}):
                time.tzset()
                item = CalendarEvent("Local", datetime(2026,3,1,2,30), datetime(2026,3,1,3,30),
                                     repeat=Recurrence("weeks", 1))
                snapshot = CalendarSnapshot((item,))
                self.assertEqual(len(snapshot.on_day(date(2026,3,8))), 1)
                os.environ["TZ"] = "America/New_York"
                time.tzset()
                self.assertFalse(snapshot.on_day(date(2026,3,8)))
        finally:
            time.tzset()


class CalendarStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = CalendarStore(Path(self.directory.name))

    def test_save_reload_conflict_delete_and_atomic_failure(self):
        snapshot, saved = self.store.save(event())
        second = CalendarStore(Path(self.directory.name))
        self.assertEqual(second.load(), snapshot)
        _, newer = second.save(replace(saved, title="Changed"))
        with self.assertRaises(CalendarConflict):
            self.store.save(replace(saved, title="Lost update"))
        with self.assertRaises(CalendarConflict):
            self.store.delete(saved)
        before = self.store.path.read_bytes()
        with patch("dok.features.calendar.store.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.store.save(replace(newer, title="Failure"))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertFalse(list(self.store.path.parent.glob(".calendar-*")))
        self.assertFalse(self.store.delete(newer).events)

    def test_reminders_are_claimed_once_across_store_instances_and_clock_rollback(self):
        _, saved = self.store.save(event(alarm=datetime(2026,1,31,8,50), repeat=Recurrence("days",1)))
        now = datetime(2026,11,7,9,tzinfo=timezone.utc)
        self.assertEqual(len(self.store.claim_due(now)[1]), 1)
        second = CalendarStore(self.store.path.parent)
        self.assertFalse(second.claim_due(now)[1])
        self.assertFalse(second.claim_due(now - timedelta(days=2))[1])
        self.assertEqual(len(second.claim_due(now + timedelta(days=1))[1]), 1)
        self.store.delete(saved)
        self.assertFalse(self.store.claim_due(now + timedelta(days=2))[1])

    def test_corrupt_storage_is_not_overwritten(self):
        self.store.path.write_text('{"bad": true}')
        with self.assertRaises(ValueError):
            self.store.save(event())
        self.assertEqual(json.loads(self.store.path.read_text()), {"bad": True})

    def test_unchanged_file_skips_decode_but_external_changes_and_deletion_are_seen(self):
        self.store.save(event())
        with patch("dok.features.calendar.store.json.loads", wraps=json.loads) as decode:
            first = self.store.load()
            self.assertIs(self.store.load(), first)
            self.assertEqual(decode.call_count, 1)
            before = self.store.path.stat()
            text = self.store.path.read_text().replace("Meeting", "Changed")
            self.store.path.write_text(text)
            os.utime(self.store.path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(self.store.load().events[0].title, "Changed")
            self.assertEqual(decode.call_count, 2)
            self.store.path.write_text('{"bad": true}')
            with self.assertRaises(ValueError):
                self.store.load()
            self.store.path.unlink()
            self.assertFalse(self.store.load().events)

    def test_failed_reminder_write_does_not_poison_cached_receipts(self):
        self.store.save(event(alarm=datetime(2026,1,31,8,50)))
        first = self.store.load()
        now = datetime(2026,1,31,9,tzinfo=timezone.utc)
        with patch("dok.features.calendar.store.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.store.claim_due(now)
        snapshot, due = self.store.claim_due(now)
        self.assertEqual(len(due), 1)
        self.assertIs(snapshot, first)
        self.assertFalse(self.store.claim_due(now)[1])
        self.assertIs(self.store.load(), first)  # Receipt-only writes keep the projection.


class CalendarRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_waits_for_io_before_the_next_read(self):
        for fail in (False, True):
            with self.subTest(worker_failure=fail), tempfile.TemporaryDirectory() as directory:
                entered, release = asyncio.Event(), threading.Event()
                loop, calls = asyncio.get_running_loop(), []
                def load():
                    calls.append(True)
                    if len(calls) == 1:
                        loop.call_soon_threadsafe(entered.set)
                        release.wait(5)
                        if fail:
                            raise OSError("cancelled read failed")
                    return CalendarSnapshot()
                runtime = CalendarRuntime(Path(directory), lambda: None, lambda *a, **k: None)
                runtime.store = SimpleNamespace(load=load)
                first = asyncio.create_task(runtime.refresh())
                second = None
                try:
                    await asyncio.wait_for(entered.wait(), 3)
                    first.cancel()
                    await asyncio.sleep(0)
                    first.cancel()
                    second = asyncio.create_task(runtime.refresh())
                    await asyncio.sleep(.03)
                    self.assertFalse(first.done())
                    self.assertEqual(len(calls), 1)
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    await asyncio.wait_for(second, 3)
                    self.assertEqual(len(calls), 2)
                finally:
                    release.set()
                    await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)

    async def test_refresh_retains_unchanged_snapshot_and_recovers_from_error(self):
        with tempfile.TemporaryDirectory() as directory:
            invalidations = []
            runtime = CalendarRuntime(Path(directory), lambda: invalidations.append(True), lambda *a, **k: None)
            saved = await runtime.save(event())
            first = runtime.snapshot
            count = len(invalidations)
            await runtime.refresh()
            await runtime.refresh()
            self.assertIs(runtime.snapshot, first)
            self.assertEqual(len(invalidations), count)
            runtime.error = "temporary"
            await runtime.refresh()
            self.assertIs(runtime.snapshot, first)
            self.assertEqual(runtime.error, "")
            self.assertEqual(len(invalidations), count + 1)
            external = CalendarStore(Path(directory))
            external.save(replace(saved, title="External"))
            await runtime.refresh()
            self.assertEqual(runtime.snapshot.events[0].title, "External")

    async def test_due_notification_restart_disabled_and_final_close(self):
        with tempfile.TemporaryDirectory() as directory:
            notified = []
            runtime = CalendarRuntime(Path(directory), lambda: None, lambda *a, **k: notified.append((a,k)))
            await runtime.save(event(alarm=datetime(2026,1,31,8,50)))
            clock = SimpleNamespace(now=lambda: datetime(2026,1,31,9,tzinfo=timezone.utc))
            enabled = False
            config = CalendarConfig(poll_s=.01)
            runtime.sync(config, lambda:clock, lambda:enabled)
            await asyncio.sleep(.04)
            self.assertFalse(notified)
            enabled = True
            async with asyncio.timeout(2):
                while not notified:
                    await asyncio.sleep(.01)
            runtime.close()
            task = runtime._slot._task
            await asyncio.gather(task, return_exceptions=True)
            restarted = CalendarRuntime(Path(directory), lambda:None, lambda *a,**k:notified.append((a,k)))
            restarted.sync(config, lambda:clock, lambda:True)
            await asyncio.sleep(.05)
            restarted.close()
            await asyncio.gather(restarted._slot._task, return_exceptions=True)
            self.assertEqual(len(notified), 1)
            self.assertEqual(notified[0][0][0], "Meeting")
