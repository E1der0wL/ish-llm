"""Google wire contract with an in-memory HTTP transport; never contacts an account."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from dok.connections import ConnectionError, ConnectionState
from dok.features.calendar.contracts import CalendarProvider, CalendarSource, CalendarWindow
from dok.integrations.google.auth import GoogleAuth
from dok.integrations.google.calendar import GoogleCalendar, GoogleConnection
from dok.integrations.google.config import GoogleAccount


SOURCE = CalendarSource(connection="google", label="Work", enabled=True, calendar="work@example.com")
WINDOW = CalendarWindow(2026, 11, "Asia/Seoul")


def row(identifier="one", **changes):
    return {"id": identifier, "summary": "Event", "start": {"dateTime": "2026-11-09T10:00:00+09:00"},
            "end": {"dateTime": "2026-11-09T11:00:00+09:00"}, **changes}


class GoogleReadTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_provider_timezone_is_interpreted_by_adapter(self):
        event = GoogleCalendar.decode(row(start={"dateTime": "2026-11-09T10:00:00", "timeZone": "Asia/Seoul"},
                                          end={"dateTime": "2026-11-09T11:00:00", "timeZone": "Asia/Seoul"}))
        self.assertEqual(event.start.utcoffset(), timedelta(hours=9))

    def connection(self, handler):
        self.auth = SimpleNamespace(token=Mock(return_value="synthetic-test-token"))
        self.connection_value = GoogleConnection("/unused", GoogleAccount(enabled=True, client_file="unused.json"),
            auth=self.auth, client=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))
        return self.connection_value.resolve(CalendarProvider)

    async def test_pages_expanded_instances_and_cancelled_events(self):
        requests = []
        def handle(request):
            requests.append(request)
            self.assertEqual(request.method, "GET")
            self.assertIn(b"work%40example.com/events", request.url.raw_path)
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic-test-token")
            self.assertEqual(request.url.params["singleEvents"], "true")
            self.assertEqual(request.url.params["orderBy"], "startTime")
            self.assertEqual(request.url.params["timeMin"], WINDOW.utc_bounds()[0])
            self.assertNotIn("syncToken", request.url.params)
            if "pageToken" not in request.url.params:
                return httpx.Response(200, json={"items": [row("series_occurrence_1")], "nextPageToken": "next"})
            self.assertEqual(request.url.params["pageToken"], "next")
            return httpx.Response(200, json={"items": [row("series_occurrence_2"),
                {"id": "cancelled", "status": "cancelled"},
                row("all-day", start={"date": "2026-11-09"}, end={"date": "2026-11-10"})]})
        provider = self.connection(handle)
        events = await provider.fetch(SOURCE, WINDOW)
        self.assertEqual([event.id for event in events], ["series_occurrence_1", "series_occurrence_2", "all-day"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.connection_value.status.state, ConnectionState.READY)

    async def test_401_refreshes_once_and_never_retries_indefinitely(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(401)
        provider = self.connection(handle)
        with self.assertRaises(ConnectionError) as raised:
            await provider.fetch(SOURCE, WINDOW)
        self.assertEqual(raised.exception.status.state, ConnectionState.AUTH_REQUIRED)
        self.assertEqual([call.args for call in self.auth.token.call_args_list], [(False,), (True,)])
        self.assertEqual(len(requests), 2)

    async def test_denial_failure_and_invalid_pages_are_not_successful_empty_calendars(self):
        for status, payload, expected in ((403, {}, ConnectionState.DENIED), (404, {}, ConnectionState.DENIED),
            (429, {}, ConnectionState.FAILED), (500, {}, ConnectionState.FAILED),
            (403, {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}, ConnectionState.FAILED),
            (200, [], ConnectionState.FAILED), (200, {"items": [None]}, ConnectionState.FAILED),
            (200, {"items": [row(start={"dateTime": "2026-11-09T10:00:00"})]}, ConnectionState.FAILED),
            (200, {"items": [], "nextPageToken": "repeated"}, ConnectionState.FAILED)):
            with self.subTest(status=status, payload=payload):
                provider = self.connection(lambda request: httpx.Response(status, json=payload))
                with self.assertRaises(ConnectionError) as raised:
                    await provider.fetch(SOURCE, WINDOW)
                self.assertEqual(raised.exception.status.state, expected)
                self.assertEqual(self.connection_value.status, raised.exception.status)

    async def test_later_page_failure_does_not_return_partial_data(self):
        def handle(request):
            return (httpx.Response(503) if "pageToken" in request.url.params else
                httpx.Response(200, json={"items": [row()], "nextPageToken": "later"}))
        with self.assertRaises(ConnectionError):
            await self.connection(handle).fetch(SOURCE, WINDOW)

    async def test_successful_empty_calendar_and_unknown_capability(self):
        events = await self.connection(lambda request: httpx.Response(200, json={})).fetch(SOURCE, WINDOW)
        self.assertEqual(events, ())
        self.assertIsNone(self.connection_value.resolve(str))


class GoogleCredentialTests(unittest.TestCase):
    def setUp(self):
        from google.oauth2.credentials import Credentials
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = GoogleAuth(self.temp.name, GoogleAccount(id="../../account", client_file="desktop.json"))
        (Path(self.temp.name) / "desktop.json").write_text('{"installed": {"client_id": "desktop-client"}}', encoding="utf-8")
        self.credentials = Credentials(token="synthetic-test-token", refresh_token="synthetic-test-refresh",
            token_uri="https://oauth2.googleapis.com/token", client_id="desktop-client", client_secret="synthetic-client",
            scopes=GoogleAuth.SCOPES, expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))

    def store(self):
        with self.auth._locked():
            self.auth._save(self.credentials)

    def test_private_atomic_credentials_stay_beneath_owned_root_and_logout_removes(self):
        self.store()
        self.assertTrue(self.auth.path.is_relative_to(Path(self.temp.name) / GoogleAuth.ROOT))
        self.assertEqual(self.auth.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.auth.connected)
        with patch.object(self.auth, "_client_config", return_value={"installed": {"client_id": "desktop-client"}}):
            self.assertEqual(self.auth.token(), self.credentials.token)
        self.auth.disconnect()
        self.assertFalse(self.auth.connected)
        with self.assertRaises(ConnectionError) as raised:
            self.auth.token()
        self.assertEqual(raised.exception.status.state, ConnectionState.AUTH_REQUIRED)

    def test_changed_client_requires_reauthentication_without_using_saved_token(self):
        self.store()
        with patch.object(self.auth, "_client_config", return_value={"installed": {"client_id": "different"}}):
            with self.assertRaises(ConnectionError) as raised:
                self.auth.token()
        self.assertEqual(raised.exception.status.state, ConnectionState.AUTH_REQUIRED)

    def test_declined_granular_scope_is_rejected(self):
        credentials = SimpleNamespace(granted_scopes=(), has_scopes=lambda scopes: True)
        with self.assertRaises(ConnectionError) as raised:
            self.auth._check_scopes(credentials)
        self.assertEqual(raised.exception.status.state, ConnectionState.DENIED)

    def test_write_permission_requires_new_grant_but_read_can_reuse_write_grant(self):
        from dataclasses import replace
        write_auth = GoogleAuth(self.temp.name, replace(self.auth.config, write=True))
        self.assertEqual(write_auth.scopes, GoogleAuth.WRITE_SCOPES)
        with self.assertRaises(ConnectionError):
            write_auth._check_scopes(self.credentials)
        write_credentials = SimpleNamespace(granted_scopes=GoogleAuth.WRITE_SCOPES,
            has_scopes=lambda scopes: scopes == GoogleAuth.WRITE_SCOPES)
        write_auth._check_scopes(write_credentials)
        self.auth._check_scopes(write_credentials)

    def test_only_desktop_client_config_is_accepted(self):
        from google_auth_oauthlib.flow import InstalledAppFlow
        path = Path(self.temp.name) / "desktop.json"
        path.write_text('{"web": {}}', encoding="utf-8")
        with patch.object(InstalledAppFlow, "from_client_config") as factory:
            with self.assertRaises(ConnectionError):
                self.auth._flow()
            factory.assert_not_called()

    def test_explicit_login_uses_loopback_pkce_flow_and_readonly_scope(self):
        from google_auth_oauthlib.flow import InstalledAppFlow
        path = Path(self.temp.name) / "desktop.json"
        path.write_text('{"installed": {"client_id": "desktop-client"}}', encoding="utf-8")
        flow = SimpleNamespace(run_local_server=Mock(return_value=self.credentials))
        with patch.object(InstalledAppFlow, "from_client_config", return_value=flow) as factory:
            self.auth.login()
        self.assertTrue(factory.call_args.kwargs["autogenerate_code_verifier"])
        self.assertEqual(factory.call_args.args[1], GoogleAuth.SCOPES)
        self.assertEqual(flow.run_local_server.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(flow.run_local_server.call_args.kwargs["port"], 0)
        self.assertTrue(self.auth.connected)
