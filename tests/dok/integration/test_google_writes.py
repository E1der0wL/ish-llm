"""Google CRUD wire semantics, conditional writes and uncertain-result handling."""
import asyncio
from dataclasses import replace
from datetime import date
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from dok.features.calendar.contracts import CalendarProvider, CalendarSource, CalendarWindow
from dok.features.calendar.writes import CalendarWriter, CalendarMutation, CalendarConflict, CalendarWriteError, EventValues, WriteKind, WriteProblem
from dok.integrations.google.calendar import GoogleCalendar, GoogleConnection
from dok.integrations.google.config import GoogleAccount


SOURCE = CalendarSource(connection="google", calendar="primary", label="Google", enabled=True)
WINDOW = CalendarWindow(2026, 11, "Asia/Seoul")


def event(**changes):
    return {"id": "existing", "etag": '"v1"', "summary": "Review", "location": "Room 1", "description": "Notes",
            "start": {"dateTime": "2026-11-10T10:00:00+09:00"},
            "end": {"dateTime": "2026-11-10T11:00:00+09:00"}, **changes}


class GoogleWriteTests(unittest.IsolatedAsyncioTestCase):
    async def setup_writer(self, handler, *, role="owner", write=True):
        self.requests = []
        def transport(request):
            self.requests.append(request)
            if request.method == "GET" and request.url.path.endswith("/events"):
                return httpx.Response(200, json={"accessRole": role, "items": [event()]})
            return handler(request)
        connection = GoogleConnection("/unused", GoogleAccount(enabled=True, write=write),
            auth=SimpleNamespace(token=Mock(return_value="synthetic-token")),
            client=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kw))
        await connection.resolve(CalendarProvider).fetch(SOURCE, WINDOW)
        self.requests.clear()
        return connection.resolve(CalendarWriter)

    async def test_create_uses_draft_id_and_update_only_sends_changed_fields_with_etag(self):
        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(request.url.params["sendUpdates"], "all")
            if request.method == "POST":
                self.assertNotIn("If-Match", request.headers)
                self.assertEqual(body["id"], "abcdef123456")
                self.assertNotIn("attendees", body)
                return httpx.Response(200, json=event(**body))
            self.assertEqual(request.method, "PATCH")
            self.assertEqual(request.headers["If-Match"], '"v1"')
            self.assertEqual(body, {"summary": "Changed"})
            return httpx.Response(200, json=event(summary="Changed", etag='"v2"'))
        writer = await self.setup_writer(handle)
        original = GoogleCalendar.decode(event())
        values = EventValues.from_event(original)
        created = await writer.apply(CalendarMutation(SOURCE, WriteKind.CREATE, "abcdef123456", values))
        self.assertEqual(created.id, "abcdef123456")
        updated = await writer.apply(CalendarMutation(SOURCE, WriteKind.UPDATE, original.id,
                                                      replace(values, title="Changed"), original))
        self.assertEqual(updated.version, '"v2"')
        self.assertEqual([request.method for request in self.requests], ["POST", "PATCH"])

    async def test_all_day_conversion_clears_datetime_and_preserves_unedited_fields(self):
        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(body["start"], {"date": "2026-11-10", "dateTime": None, "timeZone": None})
            self.assertEqual(body["end"]["date"], "2026-11-11")
            self.assertNotIn("description", body)
            self.assertNotIn("recurrence", body)
            return httpx.Response(200, json=event(start={"date": "2026-11-10"}, end={"date": "2026-11-11"}, etag='"v2"'))
        writer = await self.setup_writer(handle)
        original = GoogleCalendar.decode(event())
        values = replace(EventValues.from_event(original), start=date(2026, 11, 10), end=date(2026, 11, 11))
        await writer.apply(CalendarMutation(SOURCE, WriteKind.UPDATE, original.id, values, original))

    async def test_conflict_fetches_latest_without_second_write(self):
        def handle(request):
            return httpx.Response(412) if request.method == "PATCH" else httpx.Response(200, json=event(summary="Remote change", etag='"v2"'))
        writer = await self.setup_writer(handle)
        original = GoogleCalendar.decode(event())
        mutation = CalendarMutation(SOURCE, WriteKind.UPDATE, original.id,
                                    replace(EventValues.from_event(original), title="My change"), original)
        with self.assertRaises(CalendarConflict) as raised:
            await writer.apply(mutation)
        self.assertEqual(raised.exception.latest.version, '"v2"')
        self.assertEqual([request.method for request in self.requests], ["PATCH", "GET"])

    async def test_delete_selected_occurrence_uses_etag_and_does_not_touch_series(self):
        def handle(request):
            self.assertEqual(request.method, "DELETE")
            self.assertTrue(request.url.path.endswith("/instance"))
            self.assertEqual(request.headers["If-Match"], '"v1"')
            return httpx.Response(204)
        writer = await self.setup_writer(handle)
        original = GoogleCalendar.decode(event(id="instance", recurringEventId="series"))
        self.assertTrue(original.recurring)
        self.assertIsNone(await writer.apply(CalendarMutation(SOURCE, WriteKind.DELETE, original.id, original=original)))
        self.assertEqual(len(self.requests), 1)

    async def test_timeout_and_5xx_are_uncertain_and_never_retried(self):
        for status in (None, 503):
            def handle(request):
                if status is None:
                    raise httpx.ReadTimeout("synthetic", request=request)
                return httpx.Response(status)
            writer = await self.setup_writer(handle)
            values = EventValues.from_event(GoogleCalendar.decode(event()))
            with self.assertRaises(CalendarWriteError) as raised:
                await writer.apply(CalendarMutation(SOURCE, WriteKind.CREATE, "abcdef123456", values))
            self.assertEqual(raised.exception.problem, WriteProblem.UNCERTAIN)
            self.assertEqual(len(self.requests), 1)

    async def test_missing_write_grant_and_reader_acl_never_send_write(self):
        self.assertIsNone(await self.setup_writer(lambda request: httpx.Response(500), write=False))
        writer = await self.setup_writer(lambda request: httpx.Response(500), role="reader")
        self.assertFalse(writer.can_write(SOURCE))
        with self.assertRaises(CalendarWriteError) as raised:
            await writer.apply(CalendarMutation(SOURCE, WriteKind.CREATE, "abcdef123456", EventValues.from_event(GoogleCalendar.decode(event()))))
        self.assertEqual(raised.exception.problem, WriteProblem.DENIED)
        self.assertEqual(self.requests, [])

    async def test_forced_cancel_after_dispatch_is_uncertain(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"accessRole": "owner", "items": []})
            entered.set()
            await release.wait()
            return httpx.Response(200, json=event())
        connection = GoogleConnection("/unused", GoogleAccount(write=True), auth=SimpleNamespace(token=lambda refresh: "fake"),
            client=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))
        await connection.resolve(CalendarProvider).fetch(SOURCE, WINDOW)
        mutation = CalendarMutation(SOURCE, WriteKind.CREATE, "abcdef123456", EventValues.from_event(GoogleCalendar.decode(event())))
        task = asyncio.create_task(connection.resolve(CalendarWriter).apply(mutation))
        await entered.wait()
        task.cancel()
        with self.assertRaises(CalendarWriteError) as raised:
            await task
        self.assertEqual(raised.exception.problem, WriteProblem.UNCERTAIN)
