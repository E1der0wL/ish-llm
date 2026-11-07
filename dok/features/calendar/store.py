"""Locked atomic calendar storage. UI and scheduler use typed operations only."""
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
import fcntl
import json
import os
from pathlib import Path
import tempfile

from .models import CalendarEvent, LocalDateTime


class CalendarConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CalendarSnapshot:
    events: tuple[CalendarEvent, ...] = ()
    _queries: tuple[tuple, dict[date, tuple[CalendarEvent, ...]]] | None = field(
        default=None, init=False, repr=False, compare=False)

    def on_day(self, day: date) -> tuple[CalendarEvent, ...]:
        # Retain only the queried month. New snapshots own their own projections;
        # changing the system-local interpretation also discards cached results.
        key = (day.year, day.month, CalendarEvent.recurrence_context())
        queries = self._queries
        if queries is None or queries[0] != key:
            queries = (key, {})
            object.__setattr__(self, "_queries", queries)
        days = queries[1]
        if day not in days:
            days[day] = tuple(sorted((item for event in self.events for item in event.on_day(day)),
                                     key=lambda item: (item.start, item.title)))
        return days[day]

    def get(self, identifier):
        return next((item for item in self.events if item.id == identifier), None)


class CalendarStore:
    VERSION = 1
    FILENAME = "calendar.json"
    VERSION_FIELD = "storage_version"
    EVENTS_FIELD = "events"
    RECEIPTS_FIELD = "reminders"

    def __init__(self, directory: Path):
        self.path = Path(directory) / self.FILENAME
        self._cached: tuple[str, CalendarSnapshot, dict[str, str]] | None = None

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._cached = None
            return CalendarSnapshot(), {}
        cached = self._cached
        if cached is not None and text == cached[0]:
            return cached[1], cached[2].copy()
        # Keep checking exact file content under the lock so external writes,
        # deletion and corrupt data are observed even with unchanged file stats.
        self._cached = None
        data = json.loads(text)
        if (not isinstance(data, dict) or set(data) != {self.VERSION_FIELD, self.EVENTS_FIELD, self.RECEIPTS_FIELD}
                or type(data[self.VERSION_FIELD]) is not int or data[self.VERSION_FIELD] != self.VERSION):
            raise ValueError("Unsupported calendar storage")
        events = tuple(CalendarEvent.from_dict(item) for item in data[self.EVENTS_FIELD])
        if len({item.id for item in events}) != len(events):
            raise ValueError("Duplicate calendar event identifiers")
        receipts = data[self.RECEIPTS_FIELD]
        if not isinstance(receipts, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in receipts.items()):
            raise ValueError("Invalid reminder receipts")
        for stamp in receipts.values():
            LocalDateTime().decode(stamp)
        snapshot = cached[1] if cached is not None and events == cached[1].events else CalendarSnapshot(events)
        self._cached = text, snapshot, receipts.copy()
        return snapshot, receipts

    def _write(self, snapshot, receipts):
        data = {self.VERSION_FIELD: self.VERSION, self.EVENTS_FIELD: [item.to_dict() for item in snapshot.events], self.RECEIPTS_FIELD: receipts}
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix=".calendar-", delete=False) as stream:
                name = stream.name
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            name = None
            fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)

    def load(self):
        with self._locked():
            return self._read()[0]

    def save(self, event: CalendarEvent):
        with self._locked():
            snapshot, receipts = self._read()
            previous = snapshot.get(event.id)
            if (previous.revision if previous else 0) != event.revision:
                raise CalendarConflict("Event changed elsewhere. Reload before saving.")
            saved = replace(event, revision=event.revision + 1)
            result = CalendarSnapshot(tuple(item for item in snapshot.events if item.id != event.id) + (saved,))
            if previous:
                receipts.pop(previous.reminder_key, None)
            self._write(result, receipts)
            return result, saved

    def delete(self, event: CalendarEvent):
        with self._locked():
            snapshot, receipts = self._read()
            previous = snapshot.get(event.id)
            if previous is None or previous.revision != event.revision:
                raise CalendarConflict("Event changed elsewhere. Reload before deleting.")
            result = CalendarSnapshot(tuple(item for item in snapshot.events if item.id != event.id))
            receipts.pop(previous.reminder_key, None)
            self._write(result, receipts)
            return result

    def claim_due(self, now):
        """Durably claim the latest missed occurrence per event before enqueueing."""
        with self._locked():
            snapshot, receipts = self._read()
            due = []
            for event in snapshot.events:
                occurrence = event.latest_due(now)
                if occurrence is None:
                    continue
                stamp = occurrence.alarm.isoformat()
                if stamp > receipts.get(event.reminder_key, ""):
                    receipts[event.reminder_key] = stamp
                    due.append(occurrence)
            if due:
                self._write(snapshot, receipts)
            return snapshot, tuple(due)
