"""Conditional Google writes; never replay a request after an uncertain outcome."""
import asyncio
from datetime import datetime
from urllib.parse import quote

import httpx

from ...connections.models import ConnectionError
from ...features.calendar.writes import (
    CalendarConflict, CalendarMutation, CalendarWriteError, EventField, WriteKind, WriteProblem,
)
from .recurrence import GoogleRecurrence


class GoogleCalendarWriter:
    UPDATES = "sendUpdates"

    def __init__(self, connection, reader):
        self.connection, self.reader = connection, reader

    def can_write(self, source):
        return self.connection.config.write and self.reader.can_write(source)

    def _url(self, source, event_id=None):
        base = f"{self.reader.API}/{quote(source.calendar, safe='')}/events"
        return base + "/" + quote(event_id, safe="") if event_id else base

    def _boundary(self, value):
        return {self.reader.DATETIME if isinstance(value, datetime) else self.reader.DATE: value.isoformat()}

    def _body(self, mutation):
        fields = {EventField.TITLE: self.reader.TITLE, EventField.START: self.reader.START,
                  EventField.END: self.reader.END, EventField.LOCATION: self.reader.LOCATION,
                  EventField.NOTES: self.reader.NOTES}
        values = mutation.values
        body = {fields[field]: self._boundary(values.value(field)) if field in (EventField.START, EventField.END)
                else values.value(field) for field in mutation.fields if field in fields}
        if (EventField.REPEAT in mutation.fields or values.repeat.rule is not None
                and values.repeat.repeating
                and any(field in mutation.fields for field in (EventField.START, EventField.TIMEZONE))):
            body[GoogleRecurrence.FIELD] = GoogleRecurrence.encode(values.repeat, values.start, values.timezone)
        if isinstance(values.start, datetime):
            repeating = bool(GoogleRecurrence.encode(values.repeat, values.start, values.timezone))
            if repeating and not values.timezone:
                raise ValueError("A recurring timed event requires an IANA timezone")
            if values.timezone and (repeating or EventField.TIMEZONE in mutation.fields):
                for field, name in ((EventField.START, self.reader.START), (EventField.END, self.reader.END)):
                    body.setdefault(name, self._boundary(values.value(field)))[self.reader.TIMEZONE] = values.timezone
        # Clear the alternative time representation when switching all-day mode.
        if mutation.original is not None and type(mutation.original.start) is not type(mutation.values.start):
            for name in (self.reader.START, self.reader.END):
                body[name] = {self.reader.DATE: None, self.reader.DATETIME: None, self.reader.TIMEZONE: None, **body[name]}
        if mutation.kind == WriteKind.CREATE:
            body[self.reader.ID] = mutation.event_id
        return body

    async def get(self, source, event_id):
        try:
            return await self.reader.get(source, event_id)
        except ConnectionError as exc:
            from ...connections.models import ConnectionState
            problem = WriteProblem.DENIED if exc.status.state in (ConnectionState.DENIED, ConnectionState.AUTH_REQUIRED) else WriteProblem.FAILED
            raise CalendarWriteError(problem) from exc

    async def apply(self, mutation: CalendarMutation):
        if not self.can_write(mutation.source):
            raise CalendarWriteError(WriteProblem.DENIED)
        if mutation.kind == WriteKind.UPDATE and not mutation.fields:
            return mutation.original
        sent = False
        try:
            async with self.reader.http.session() as client:
                method = {WriteKind.CREATE: "POST", WriteKind.UPDATE: "PATCH", WriteKind.DELETE: "DELETE"}[mutation.kind]
                url = self._url(mutation.source, None if mutation.kind == WriteKind.CREATE else mutation.event_id)
                options = {"params": {self.UPDATES: self.connection.config.updates.value}}
                if mutation.original is not None:
                    options["headers"] = {"If-Match": mutation.original.version}
                if mutation.kind != WriteKind.DELETE:
                    options["json"] = self._body(mutation)
                sent = True
                response = await client.request(method, url, **options)
            if response.status_code in (409, 412):
                try:
                    latest = await self.get(mutation.source, mutation.event_id)
                except CalendarWriteError:
                    latest = None
                raise CalendarConflict(latest)
            if response.status_code in (401, 403):
                raise CalendarWriteError(WriteProblem.DENIED)
            if response.status_code in (404, 410):
                raise CalendarWriteError(WriteProblem.UNAVAILABLE)
            if response.status_code >= 500:
                raise CalendarWriteError(WriteProblem.UNCERTAIN)
            response.raise_for_status()
            if mutation.kind == WriteKind.DELETE:
                return None
            try:
                result = self.reader.decode(response.json())
                if result is None or not result.version or result.id != mutation.event_id:
                    raise ValueError("Google write response has no matching version")
                return result
            except (ValueError, KeyError, TypeError):
                raise CalendarWriteError(WriteProblem.UNCERTAIN) from None
        except asyncio.CancelledError:
            if sent:
                raise CalendarWriteError(WriteProblem.UNCERTAIN) from None
            raise
        except ConnectionError as exc:
            raise CalendarWriteError(WriteProblem.DENIED) from exc
        except httpx.TransportError as exc:
            raise CalendarWriteError(WriteProblem.UNCERTAIN if sent else WriteProblem.FAILED) from exc
        except httpx.HTTPStatusError as exc:
            raise CalendarWriteError(WriteProblem.FAILED) from exc
