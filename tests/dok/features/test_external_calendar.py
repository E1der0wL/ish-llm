"""Calendar read composition, cancellation and separation from local scheduling."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import unittest
from unittest.mock import Mock, patch

from dok.connections import ConnectionError, ConnectionRegistry, ConnectionSpec, ConnectionState, ConnectionStatus
from dok.features.calendar.config import CalendarConfig
from dok.features.calendar.contracts import CalendarProvider, CalendarSource, CalendarWindow, ExternalEvent
from dok.features.calendar.models import CalendarEvent
from dok.features.calendar.actions import ActionCatalog
from dok.features.calendar.service import CalendarListing, CalendarService
from dok.features.calendar.store import CalendarSnapshot
from dok.features.calendar.view import CalendarPage
from dok.core.theme import Theme
from dok.locales import Locale
from dok.rendering.text import width


SOURCE = CalendarSource(connection="google", calendar="primary", label="Google", enabled=True)
WINDOW = CalendarWindow(2026, 11, "Asia/Seoul")


def external(identifier="remote"):
    return ExternalEvent(identifier, "Remote event", datetime(2026, 11, 9, 1, tzinfo=timezone.utc),
                         datetime(2026, 11, 9, 2, tzinfo=timezone.utc))


class Provider:
    def __init__(self):
        self.calls = []
        self.events = (external(),)
        self.failure = None
        self.gate = None
        self.cancelled = False
        self.status = ConnectionStatus(ConnectionState.READY)

    def resolve(self, capability):
        return self if capability is CalendarProvider else None

    async def fetch(self, source, window):
        self.calls.append((source, window))
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.failure:
                raise ConnectionError(self.failure)
            return self.events
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("Calendar worker did not reach expected state")


class CalendarServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = Provider()
        self.registry = ConnectionRegistry()
        self.registry.configure((ConnectionSpec(SOURCE.connection, "fake", True, 1, lambda: self.provider),))
        self.local = SimpleNamespace(snapshot=CalendarSnapshot(), actions=ActionCatalog(), set_external=Mock())
        self.service = CalendarService(self.local, self.registry, lambda: None)

    async def asyncTearDown(self):
        self.service.close()
        await until(lambda: not self.service._slot.running)

    async def load(self):
        self.service.sync((SOURCE,), WINDOW)
        await until(lambda: self.service.sources[0].status.state == ConnectionState.READY)
        await until(lambda: not self.service.working)

    async def test_visible_window_is_shared_and_success_reused_on_reopen(self):
        await self.load()
        for _ in range(30):
            self.service.sync((SOURCE,), WINDOW)
            self.service.snapshot.on_day(date(2026, 11, 9))
        self.assertEqual(len(self.provider.calls), 1)
        self.service.sync((SOURCE,), None)
        await until(lambda: not self.service._slot.running)
        self.service.sync((SOURCE,), WINDOW)
        await asyncio.sleep(0)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.local.snapshot.events, ())

    async def test_expiry_refreshes_only_while_visible_and_full_response_replaces(self):
        with patch("dok.features.calendar.service.time", SimpleNamespace(monotonic=lambda: 100)):
            await self.load()
        self.service.sync((SOURCE,), None)
        await until(lambda: not self.service._slot.running)
        self.provider.events = ()
        with patch("dok.features.calendar.service.time", SimpleNamespace(monotonic=lambda: 100 + SOURCE.refresh_s + 1)):
            self.service.sync((SOURCE,), WINDOW)
            await until(lambda: len(self.provider.calls) == 2 and not self.service.working)
        self.assertEqual(self.service.snapshot.external, ())

    async def test_failure_keeps_cache_without_polling_and_reopen_retries(self):
        await self.load()
        self.provider.failure = ConnectionState.FAILED
        self.service.refresh()
        await until(lambda: self.service.sources[0].stale)
        self.assertEqual(len(self.service.snapshot.external), 1)
        for _ in range(30):
            self.service.sync((SOURCE,), WINDOW)
            await asyncio.sleep(0)
        self.assertEqual(len(self.provider.calls), 2)
        self.service.sync((SOURCE,), None)
        await until(lambda: not self.service._slot.running)
        self.provider.failure = None
        self.service.sync((SOURCE,), WINDOW)
        await until(lambda: self.service.sources[0].status.state == ConnectionState.READY)
        self.assertEqual(len(self.provider.calls), 3)
        self.assertFalse(self.service.sources[0].stale)

    async def test_authorization_failure_clears_private_cache(self):
        await self.load()
        self.provider.failure = ConnectionState.AUTH_REQUIRED
        self.service.refresh()
        await until(lambda: self.service.sources[0].status.state == ConnectionState.AUTH_REQUIRED)
        self.assertEqual(self.service.snapshot.external, ())
        self.assertFalse(self.service.sources[0].stale)

    async def test_hidden_cancels_inflight_read_and_month_change_cannot_publish_old_result(self):
        self.provider.gate = asyncio.Event()
        self.service.sync((SOURCE,), WINDOW)
        await until(lambda: len(self.provider.calls) == 1)
        december = CalendarWindow(2026, 12, "Asia/Seoul")
        self.service.sync((SOURCE,), december)
        await until(lambda: not self.service._slot.running)
        self.assertTrue(self.provider.cancelled)
        self.assertEqual(self.service.snapshot.external, ())
        self.provider.gate = None
        self.service.sync((SOURCE,), december)
        await until(lambda: self.service.sources[0].status.state == ConnectionState.READY)
        self.assertEqual(self.provider.calls[-1][1], december)
        self.service.sync((SOURCE,), None)
        await until(lambda: not self.service._slot.running)

    async def test_disable_connection_discards_cache_without_provider_call(self):
        await self.load()
        self.registry.configure((ConnectionSpec(SOURCE.connection, "fake", False, 1, lambda: self.provider),))
        self.service.sync((SOURCE,), WINDOW)
        await until(lambda: not self.service._slot.running)
        self.service.sync((SOURCE,), WINDOW)
        await until(lambda: self.service.sources[0].status.state == ConnectionState.DISABLED)
        self.assertEqual(self.service.snapshot.external, ())
        self.assertEqual(len(self.provider.calls), 1)

    async def test_host_task_cancellation_stops_polling_without_a_final_render(self):
        tasks = []
        def start(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task
        self.service.sync((SOURCE,), WINDOW, start_task=start)
        await until(lambda: self.service.sources[0].status.state == ConnectionState.READY)
        for task in tasks:
            task.cancel()
        await until(lambda: not self.service._slot.running)
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertFalse(self.service._active)


class CalendarReadContractsTests(unittest.TestCase):
    def test_folded_hour_keeps_original_offsets_and_sorts_with_local_wall_time(self):
        zone = ZoneInfo("America/New_York")
        event = ExternalEvent("fold", "DST", datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0),
                              datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1))
        entry = event.project(SOURCE, "America/New_York")
        self.assertNotEqual(entry.start.utcoffset(), entry.end.utcoffset())
        self.assertTrue(entry.on_day(date(2026, 11, 1)))
        local = CalendarEvent(title="Local", start=datetime(2026, 11, 1, 2), end=datetime(2026, 11, 1, 3),
                              timezone="America/New_York")
        self.assertEqual(CalendarListing(CalendarSnapshot(events=(local,)), (entry,)).on_day(date(2026, 11, 1))[0], entry)

    def test_all_day_end_exclusive_and_timed_events_use_selected_timezone(self):
        all_day = ExternalEvent("holiday", "Trip", date(2026, 11, 9), date(2026, 11, 11)).project(SOURCE, "America/New_York")
        self.assertTrue(all_day.on_day(date(2026, 11, 10)))
        self.assertFalse(all_day.on_day(date(2026, 11, 11)))
        timed = external().project(SOURCE, "Asia/Seoul")
        self.assertEqual(timed.start.hour, 10)
        self.assertEqual(WINDOW.utc_bounds(), ("2026-10-31T15:00:00+00:00", "2026-11-30T15:00:00+00:00"))
        with self.assertRaises(ValueError):
            ExternalEvent("bad", "Naive", datetime(2026, 1, 1), datetime(2026, 1, 2))

    def test_equal_ids_from_different_sources_do_not_collide_with_local_events(self):
        local = CalendarEvent(id="remote", title="Local", start=datetime(2026, 11, 9, 11), end=datetime(2026, 11, 9, 12))
        first = external().project(SOURCE, "Asia/Seoul")
        other = external().project(replace(SOURCE, connection="other"), "Asia/Seoul")
        listing = CalendarListing(CalendarSnapshot(events=(local,)), (first, other))
        self.assertEqual(len(listing.on_day(date(2026, 11, 9))), 3)
        self.assertIs(listing.get(local.id), local)
        self.assertIs(listing.get(first.id), first)
        self.assertIs(listing.get(other.id), other)

    def test_remote_detail_never_enters_local_save_delete_or_reminder_path(self):
        local = SimpleNamespace(snapshot=CalendarSnapshot(), error="", save=Mock(), delete=Mock())
        service = SimpleNamespace(snapshot=CalendarListing(local.snapshot, (external().project(SOURCE, "Asia/Seoul"),)),
                                  edits=SimpleNamespace(destinations=(), editable=lambda identifier: False),
                                  sources=(), refresh=Mock(), working=False)
        page = CalendarPage(lambda: SimpleNamespace(now=lambda: datetime(2026, 11, 9), timezone="Asia/Seoul"),
                            CalendarConfig, local, calendar=service, locale=lambda: Locale.resolve("en"))
        page.key("enter", "")
        page.key("enter", "")  # Open the selected read-only entry from the day list.
        self.assertIsNone(page.draft)
        page.key("f2", "")
        page.key("delete", "")
        page._save(delete=True)
        local.save.assert_not_called()
        local.delete.assert_not_called()
        self.assertIn("Read only", page.status_text())
        for columns, rows in ((136, 38), (65, 25), (8, 3), (1, 1)):
            body = page.draw_body(columns, rows, Theme())
            self.assertLessEqual(len(body), rows)
            self.assertTrue(all(sum(width(part[1]) for part in row) <= columns for row in body))
        page.key("f5", "")
        service.refresh.assert_called_once()
        page.key("escape", "")
        page.key("any", "n")
        self.assertIsNotNone(page.draft)
        self.assertIsNone(page.draft.original)
        self.assertIsNone(page._external)
