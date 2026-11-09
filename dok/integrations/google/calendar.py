"""Google Calendar v3 read adapter: month windows, paginated expanded occurrences."""
import asyncio
import time
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from ...connections.models import ConnectionError, ConnectionState, ConnectionStatus
from ...core.tasks import run_blocking
from ...features.calendar.contracts import CalendarProvider, ExternalEvent
from ...features.calendar.writes import CalendarWriter
from .auth import GoogleAuth
from .transport import GoogleTransport
from .recurrence import GoogleRecurrence


class GoogleCalendar:
    API = "https://www.googleapis.com/calendar/v3/calendars"
    # Google API wire declarations belong here, never in the calendar service/UI.
    ITEMS, NEXT_PAGE, ID, STATUS = "items", "nextPageToken", "id", "status"
    START, END, DATE, DATETIME = "start", "end", "date", "dateTime"
    TIMEZONE = "timeZone"
    VERSION, RECURRING, LOCKED, EVENT_TYPE = "etag", "recurringEventId", "locked", "eventType"
    ORIGINAL_START = "originalStartTime"
    ACCESS = "accessRole"
    WRITE_ROLES = frozenset(("writer", "owner"))
    TITLE, LOCATION, NOTES, LINK = "summary", "location", "description", "htmlLink"
    CANCELLED = "cancelled"
    ERROR, ERRORS, REASON = "error", "errors", "reason"
    QUOTA_REASONS = frozenset(("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"))

    def __init__(self, connection, *, client=httpx.AsyncClient):
        self.connection = connection
        self.http = GoogleTransport(connection, client)
        self._roles = {}
        self._zones = {}
        self._zone_until = {}

    def _remember_zone(self, source, value):
        ZoneInfo(value)
        self._zones[source.identity] = value
        self._zone_until[source.identity] = time.monotonic() + source.refresh_s

    def can_write(self, source):
        return self._roles.get(source.identity) in self.WRITE_ROLES

    @classmethod
    def _boundary(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Missing Google event boundary")
        if cls.DATETIME in value:
            result = datetime.fromisoformat(value[cls.DATETIME])
            if result.utcoffset() is None and cls.TIMEZONE in value:
                result = result.replace(tzinfo=ZoneInfo(value[cls.TIMEZONE]))
            return result
        return date.fromisoformat(value[cls.DATE])

    @classmethod
    def decode(cls, row, default_zone=None):
        if not isinstance(row, dict):
            raise ValueError("Invalid Google event")
        if row.get(cls.STATUS) == cls.CANCELLED:
            return None
        start = cls._boundary(row[cls.START])
        zone = row[cls.START].get(cls.TIMEZONE) or default_zone
        repeat = GoogleRecurrence.decode(row.get(GoogleRecurrence.FIELD, []), start, zone)
        return ExternalEvent(row[cls.ID], row.get(cls.TITLE, ""),
            start, cls._boundary(row[cls.END]),
            row.get(cls.LOCATION, ""), row.get(cls.NOTES, ""), row.get(cls.LINK, ""), row.get(cls.VERSION, ""),
            bool(row.get(cls.RECURRING) or repeat.encoded),
            bool(row.get(cls.LOCKED)) or row.get(cls.EVENT_TYPE, "default") != "default",
            row.get(cls.RECURRING, ""), cls._boundary(row[cls.ORIGINAL_START]) if cls.ORIGINAL_START in row else None,
            repeat, zone)

    def _url(self, source, event_id):
        return f"{self.API}/{quote(source.calendar, safe='')}/events/{quote(event_id, safe='')}"

    async def get(self, source, event_id):
        try:
            async with self.http.session() as client:
                response = await client.get(self._url(source, event_id))
            if response.status_code in (404, 410):
                return None
            if response.status_code in (401, 403):
                raise ConnectionError(self._denial(response), "Google calendar access is unavailable")
            response.raise_for_status()
            row = response.json()
            if not isinstance(row, dict):
                raise ValueError("Invalid Google event response")
            if (row.get(self.STATUS) != self.CANCELLED and self.DATE in row.get(self.START, {})
                    and time.monotonic() >= self._zone_until.get(source.identity, 0)):
                async with self.http.session() as client:
                    # events.list exposes the calendar zone with the existing
                    # events scope; calendars.get would need broader permission.
                    info = await client.get(f"{self.API}/{quote(source.calendar, safe='')}/events",
                                            params={"maxResults": 1, "fields": self.TIMEZONE})
                if info.status_code in (401, 403):
                    raise ConnectionError(self._denial(info), "Google calendar access is unavailable")
                info.raise_for_status()
                self._remember_zone(source, info.json()[self.TIMEZONE])
            return self.decode(row, self._zones.get(source.identity))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ConnectionError(ConnectionState.FAILED, "Google calendar request failed") from exc

    async def instances(self, source, event_id, lower, upper):
        params = {"timeMin": lower.isoformat(), "timeMax": upper.isoformat(), "showDeleted": "false"}
        result, seen = {}, set()
        try:
            async with self.http.session() as client:
                while True:
                    response = await client.get(self._url(source, event_id) + "/instances", params=params)
                    if response.status_code in (404, 410):
                        return ()
                    if response.status_code in (401, 403):
                        raise ConnectionError(self._denial(response), "Google calendar access is unavailable")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get(self.ITEMS, []), list):
                        raise ValueError("Invalid Google instance list")
                    if self.TIMEZONE in payload:
                        self._remember_zone(source, payload[self.TIMEZONE])
                    for row in payload.get(self.ITEMS, []):
                        event = self.decode(row, self._zones.get(source.identity))
                        if event is not None:
                            result[event.id] = event
                    page = payload.get(self.NEXT_PAGE)
                    if not page:
                        break
                    if not isinstance(page, str) or page in seen:
                        raise ValueError("Invalid Google pagination token")
                    seen.add(page)
                    params["pageToken"] = page
            return tuple(result.values())
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ConnectionError(ConnectionState.FAILED, "Google calendar request failed") from exc

    @classmethod
    def _denial(cls, response):
        if response.status_code == 401:
            return ConnectionState.AUTH_REQUIRED
        try:
            reasons = {item.get(cls.REASON) for item in response.json()[cls.ERROR][cls.ERRORS]}
        except (ValueError, KeyError, TypeError, AttributeError):
            reasons = set()
        return ConnectionState.FAILED if reasons & cls.QUOTA_REASONS else ConnectionState.DENIED

    async def fetch(self, source, window):
        try:
            lower, upper = window.utc_bounds()
            params = {"timeMin": lower, "timeMax": upper, "singleEvents": "true", "orderBy": "startTime",
                      "showDeleted": "false"}
            result, seen = {}, set()
            role = None
            async with self.http.session() as client:
                while True:
                    response = await client.get(f"{self.API}/{quote(source.calendar, safe='')}/events",
                        params=params)
                    if response.status_code in (401, 403, 404):
                        state = self._denial(response)
                        raise ConnectionError(state, "Google calendar access is unavailable")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid Google response")
                    if self.TIMEZONE in payload:
                        self._remember_zone(source, payload[self.TIMEZONE])
                    if self.ACCESS in payload:
                        role = payload[self.ACCESS]
                        if not isinstance(role, str):
                            raise ValueError("Invalid Google calendar access role")
                    rows = payload.get(self.ITEMS, [])
                    if not isinstance(rows, list):
                        raise ValueError("Invalid Google event list")
                    for row in rows:
                        event = self.decode(row, self._zones.get(source.identity))
                        if event is not None:
                            result[event.id] = event
                    page = payload.get(self.NEXT_PAGE)
                    if not page:
                        break
                    if not isinstance(page, str) or page in seen:
                        raise ValueError("Invalid Google pagination token")
                    seen.add(page)
                    params["pageToken"] = page
            self._roles[source.identity] = role
            self.connection.publish(ConnectionStatus(ConnectionState.READY))
            return tuple(result.values())
        except ConnectionError as exc:
            self.connection.publish(exc.status)
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            failure = ConnectionError(ConnectionState.FAILED, "Google calendar request failed")
            self.connection.publish(failure.status)
            raise failure from exc


class GoogleConnection:
    def __init__(self, directory, config, *, auth=None, client=httpx.AsyncClient):
        self.config = config
        self._auth = auth or GoogleAuth(directory, config)
        self._status = ConnectionStatus(ConnectionState.AUTH_REQUIRED if config.client_file else ConnectionState.SETUP)
        self._lock = asyncio.Lock()
        self._calendar = GoogleCalendar(self, client=client)
        from .writer import GoogleCalendarWriter
        self._writer = GoogleCalendarWriter(self, self._calendar)

    @property
    def status(self):
        return self._status

    def publish(self, status):
        self._status = status

    async def token(self, refresh=False):
        async with self._lock:
            try:
                return await run_blocking(self._auth.token, refresh)
            except ImportError as exc:
                raise ConnectionError(ConnectionState.SETUP, "Install the google-auth-oauthlib dependency") from exc

    def resolve(self, capability):
        if capability is CalendarWriter:
            return self._writer if self.config.write else None
        return self._calendar if capability is CalendarProvider else None
