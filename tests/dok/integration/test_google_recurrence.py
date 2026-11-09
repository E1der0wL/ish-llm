"""Google recurrence codecs and real adapter HTTP requests with synthetic transport."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
import httpx

from dok.features.calendar.contracts import CalendarProvider, ExternalRecurrence
from dok.features.calendar.recurrence import Recurrence, RepeatUnit, RepeatEnd
from dok.features.calendar.writes import CalendarMutation, EventValues, WriteKind
from dok.integrations.google.calendar import GoogleCalendar, GoogleConnection
from dok.integrations.google.config import GoogleAccount
from dok.integrations.google.recurrence import GoogleRecurrence
from dok.integrations.google.writer import GoogleCalendarWriter
from tests.dok.integration import test_google_writes as google_writes

SOURCE, event = google_writes.SOURCE, google_writes.event


class RecurrenceCodecTests(unittest.TestCase):
    def test_count_until_all_day_and_shorter_month_clamping_roundtrip(self):
        start = datetime(2026, 1, 31, 10, tzinfo=timezone.utc)
        for rule in (Recurrence(RepeatUnit.DAYS, count=3), Recurrence(RepeatUnit.WEEKS, 2),
                     Recurrence(RepeatUnit.MONTHS, count=12), Recurrence(RepeatUnit.YEARS, count=2),
                     Recurrence(RepeatUnit.WEEKS, end=RepeatEnd.DATE, until=date(2027, 1, 1))):
            for origin in (start, start.date()):
                with self.subTest(rule=rule, origin=origin):
                    rows = GoogleRecurrence.encode(ExternalRecurrence(rule), origin, "UTC")
                    decoded = GoogleRecurrence.decode(rows, origin, "UTC")
                    self.assertEqual(decoded.rule, rule)
        rows = GoogleRecurrence.encode(ExternalRecurrence(Recurrence(RepeatUnit.MONTHS)), start, "UTC")
        self.assertIn("BYMONTHDAY=28,29,30,31;BYSETPOS=-1", rows[0])

    def test_custom_rules_and_exclusions_are_preserved_without_loss(self):
        rows = ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR", "EXDATE:20261111T010000Z"]
        start = datetime(2026, 11, 9, 1, tzinfo=timezone.utc)
        spec = GoogleRecurrence.decode(rows, start, "UTC")
        self.assertFalse(spec.editable)
        self.assertEqual(GoogleRecurrence.encode(spec, start, "UTC"), rows)

    def test_instances_keep_stable_parent_and_original_start_when_moved(self):
        decoded = GoogleCalendar.decode(event(id="instance", recurringEventId="parent",
            originalStartTime={"dateTime": "2026-11-09T10:00:00+09:00"}))
        self.assertEqual(decoded.series_id, "parent")
        self.assertEqual(decoded.original_start.day, 9)
        self.assertEqual(decoded.start.day, 10)
        self.assertIsNone(GoogleCalendar.decode({"id": "cancelled", "status": "cancelled"}))


class GoogleRecurrenceWriteTests(unittest.IsolatedAsyncioTestCase):
    setup_writer = google_writes.GoogleWriteTests.setup_writer

    async def test_all_day_zone_uses_events_scope_and_refreshes_without_repeated_metadata_reads(self):
        zone, metadata_reads = "Asia/Seoul", []
        def handle(request):
            self.assertIn("/events", request.url.path)
            if request.url.path.endswith("/events"):
                self.assertEqual(request.url.params["fields"], "timeZone")
                metadata_reads.append(request)
                return httpx.Response(200, json={"timeZone": zone})
            return httpx.Response(200, json=event(start={"date": "2026-11-10"}, end={"date": "2026-11-11"}))
        connection = GoogleConnection("/unused", GoogleAccount(enabled=True),
            auth=SimpleNamespace(token=lambda refresh: "synthetic-token"),
            client=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw))
        reader = connection.resolve(CalendarProvider)
        self.assertEqual((await reader.get(SOURCE, "existing")).timezone, zone)
        self.assertEqual((await reader.get(SOURCE, "existing")).timezone, zone)
        self.assertEqual(len(metadata_reads), 1)
        reader._zone_until[SOURCE.identity] = 0
        zone = "America/New_York"
        self.assertEqual((await reader.get(SOURCE, "existing")).timezone, zone)
        self.assertEqual(len(metadata_reads), 2)
    async def test_rrule_and_timezone_are_sent_without_dok_actions(self):
        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(body[GoogleRecurrence.FIELD], ["RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=12"])
            self.assertEqual(body["start"]["timeZone"], "Asia/Seoul")
            self.assertFalse({"alarms", "command", "reminders"} & body.keys())
            return httpx.Response(200, json=event(**body))
        writer = await self.setup_writer(handle)
        original = GoogleCalendar.decode(event())
        values = replace(EventValues.from_event(original), repeat=ExternalRecurrence(Recurrence(RepeatUnit.WEEKS, count=12)),
                         timezone="Asia/Seoul")
        await writer.apply(CalendarMutation(SOURCE, WriteKind.UPDATE, original.id, values, original))

    async def test_instances_paginate_and_omit_cancelled_exceptions(self):
        def handle(request):
            if request.url.params.get("pageToken") == "next":
                return httpx.Response(200, json={"items": [{"id": "cancelled", "status": "cancelled"},
                    event(id="second", recurringEventId="parent")]})
            return httpx.Response(200, json={"items": [event(id="first", recurringEventId="parent")], "nextPageToken": "next"})
        writer = await self.setup_writer(handle)
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        rows = await writer.reader.instances(SOURCE, "parent", now, now + timedelta(days=1))
        self.assertEqual([row.id for row in rows], ["first", "second"])
        self.assertEqual(len(self.requests), 2)
