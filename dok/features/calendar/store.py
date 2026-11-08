"""Locked atomic calendar storage. UI and scheduler use typed operations only."""
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile

from .models import CalendarEvent, LocalDateTime, ReminderOccurrence
from .commands import CommandOccurrence


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


@dataclass(frozen=True, slots=True)
class CalendarCheck:
    snapshot: CalendarSnapshot
    due: tuple[ReminderOccurrence, ...] = ()
    next_at: datetime | None = None
    commands: tuple[CommandOccurrence, ...] = ()


class CalendarStore:
    VERSION = 3
    MULTI_ALARM_VERSION = 2
    SINGLE_ALARM_VERSION = 1
    FILENAME = "calendar.json"
    VERSION_FIELD = "storage_version"
    EVENTS_FIELD = "events"
    RECEIPTS_FIELD = "reminders"
    COMMANDS_FIELD = "commands"

    def __init__(self, directory: Path):
        self.path = Path(directory) / self.FILENAME
        self._cached: tuple[str, CalendarSnapshot, dict[str, str], dict[str, str]] | None = None
        self._plan = None
        self._command_watches: dict[str, datetime] = {}

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
            return CalendarSnapshot(), {}, {}
        cached = self._cached
        if cached is not None and text == cached[0]:
            return cached[1], cached[2].copy(), cached[3].copy()
        # Keep checking exact file content under the lock so external writes,
        # deletion and corrupt data are observed even with unchanged file stats.
        self._cached = None
        data = json.loads(text)
        if (not isinstance(data, dict) or type(data.get(self.VERSION_FIELD)) is not int
                or data[self.VERSION_FIELD] not in (self.VERSION, self.MULTI_ALARM_VERSION, self.SINGLE_ALARM_VERSION)):
            raise ValueError("Unsupported calendar storage")
        expected = {self.VERSION_FIELD, self.EVENTS_FIELD, self.RECEIPTS_FIELD}
        if data[self.VERSION_FIELD] == self.VERSION:
            expected.add(self.COMMANDS_FIELD)
        if set(data) != expected:
            raise ValueError("Unsupported calendar storage fields")
        single_alarm = data[self.VERSION_FIELD] == self.SINGLE_ALARM_VERSION
        decode = CalendarEvent.from_single_alarm if single_alarm else CalendarEvent.from_dict
        events = tuple(decode(item) for item in data[self.EVENTS_FIELD])
        if len({item.id for item in events}) != len(events):
            raise ValueError("Duplicate calendar event identifiers")
        receipts = data[self.RECEIPTS_FIELD]
        commands = data[self.COMMANDS_FIELD] if data[self.VERSION_FIELD] == self.VERSION else {}
        for values in (receipts, commands):
            if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                raise ValueError("Invalid calendar delivery receipts")
            for stamp in values.values():
                LocalDateTime().decode(stamp)
        if single_alarm:
            # Preserve the delivered occurrence when upgrading to per-offset receipts.
            receipts = {event.reminder_key(alarm): receipts[key] for event in events
                        for alarm in event.alarms if (key := event.reminder_series) in receipts}
        snapshot = cached[1] if cached is not None and events == cached[1].events else CalendarSnapshot(events)
        self._cached = text, snapshot, receipts.copy(), commands.copy()
        return snapshot, receipts, commands

    def _write(self, snapshot, receipts, commands):
        data = {self.VERSION_FIELD: self.VERSION, self.EVENTS_FIELD: [item.to_dict() for item in snapshot.events],
                self.RECEIPTS_FIELD: receipts, self.COMMANDS_FIELD: commands}
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
            snapshot, receipts, commands = self._read()
            previous = snapshot.get(event.id)
            if (previous.revision if previous else 0) != event.revision:
                raise CalendarConflict("Event changed elsewhere. Reload before saving.")
            saved = replace(event, revision=event.revision + 1)
            result = CalendarSnapshot(tuple(item for item in snapshot.events if item.id != event.id) + (saved,))
            if previous:
                commands.pop(previous.command_key, None)
                for alarm in previous.alarms:
                    receipts.pop(previous.reminder_key(alarm), None)
            self._write(result, receipts, commands)
            return result, saved

    def delete(self, event: CalendarEvent):
        with self._locked():
            snapshot, receipts, commands = self._read()
            previous = snapshot.get(event.id)
            if previous is None or previous.revision != event.revision:
                raise CalendarConflict("Event changed elsewhere. Reload before deleting.")
            result = CalendarSnapshot(tuple(item for item in snapshot.events if item.id != event.id))
            commands.pop(previous.command_key, None)
            for alarm in previous.alarms:
                receipts.pop(previous.reminder_key(alarm), None)
            self._write(result, receipts, commands)
            return result

    def claim_due(self, now):
        """Claim due reminders; retain the existing event-oriented result API."""
        check = self.check(now)
        return check.snapshot, tuple(item.event for item in check.due)

    def reset_command_watch(self):
        """Arm only future starts when a command scheduler begins/restarts."""
        self._command_watches.clear()
        self._plan = None

    def check(self, now, deliver=True, commands=False):
        """Read external changes and reuse the next deadline until it is reached.

        The store owns receipts and plan invalidation. No consumer reads a receipt
        dictionary or interprets the file to decide which reminder is pending.
        """
        now = now.astimezone(timezone.utc)
        with self._locked():
            snapshot, receipts, command_receipts = self._read()
            command_events = tuple(event for event in snapshot.events if commands and event.command.strip())
            self._command_watches = {event.command_key: self._command_watches.get(event.command_key, now)
                                     for event in command_events}
            def plan_key():
                return (snapshot, tuple(sorted(receipts.items())), tuple(sorted(command_receipts.items())),
                        tuple(sorted(self._command_watches.items())), CalendarEvent.recurrence_context(), deliver, commands)
            key = plan_key()
            plan = self._plan
            if plan is not None and plan[0] == key and now >= plan[1] and (plan[2] is None or now < plan[2]):
                return CalendarCheck(snapshot, next_at=plan[2])
            due, future, command_due = [], [], []
            if deliver:
                for event in snapshot.events:
                    for alarm in event.alarms:
                        occurrence = event.latest_due(now, alarm)
                        if occurrence and occurrence.stamp > receipts.get(event.reminder_key(alarm), ""):
                            receipts[event.reminder_key(alarm)] = occurrence.stamp
                            due.append(occurrence)
                        following = event.next_due(now, alarm)
                        if following is not None:
                            future.append(following)
            for event in command_events:
                occurrence = CommandOccurrence.latest(event, now)
                if (occurrence and occurrence.at > self._command_watches[event.command_key]
                        and occurrence.stamp > command_receipts.get(event.command_key, "")):
                    # Persist admission before submitting or reporting a busy skip.
                    command_receipts[event.command_key] = occurrence.stamp
                    command_due.append(occurrence)
                following = CommandOccurrence.next_at(event, now)
                if following is not None:
                    future.append(following)
            if due or command_due:
                self._write(snapshot, receipts, command_receipts)
            next_at = min(future) if future else None
            key = plan_key()
            self._plan = key, now, next_at
            return CalendarCheck(snapshot, tuple(sorted(due, key=lambda item: item.at)), next_at,
                                 tuple(sorted(command_due, key=lambda item: item.at)))
