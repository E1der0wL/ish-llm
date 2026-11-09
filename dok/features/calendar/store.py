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
from .actions import ActionSettings, ActionBinding, ActionCatalog
from .scheduler import CalendarScheduler


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
    actions: ActionCatalog = ActionCatalog()


class CalendarStore:
    VERSION = 4
    COMBINED_VERSION = 3
    MULTI_ALARM_VERSION = 2
    SINGLE_ALARM_VERSION = 1
    FILENAME = "calendar.json"
    VERSION_FIELD = "storage_version"
    EVENTS_FIELD = "events"
    RECEIPTS_FIELD = "reminders"
    COMMANDS_FIELD = "commands"
    ACTIONS_FIELD = "actions"
    EXTERNAL_FIELD = "external_actions"

    def __init__(self, directory: Path):
        self.path = Path(directory) / self.FILENAME
        self._cached: tuple[str, CalendarSnapshot, dict[str, str], dict[str, str], ActionCatalog] | None = None
        self._scheduler = CalendarScheduler()

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
            return CalendarSnapshot(), {}, {}, ActionCatalog()
        cached = self._cached
        if cached is not None and text == cached[0]:
            return cached[1], cached[2].copy(), cached[3].copy(), cached[4]
        # Keep checking exact file content under the lock so external writes,
        # deletion and corrupt data are observed even with unchanged file stats.
        self._cached = None
        data = json.loads(text)
        if (not isinstance(data, dict) or type(data.get(self.VERSION_FIELD)) is not int
                or data[self.VERSION_FIELD] not in (self.VERSION, self.COMBINED_VERSION, self.MULTI_ALARM_VERSION, self.SINGLE_ALARM_VERSION)):
            raise ValueError("Unsupported calendar storage")
        expected = {self.VERSION_FIELD, self.EVENTS_FIELD, self.RECEIPTS_FIELD}
        if data[self.VERSION_FIELD] >= self.COMBINED_VERSION:
            expected.add(self.COMMANDS_FIELD)
        if data[self.VERSION_FIELD] == self.VERSION:
            expected.update((self.ACTIONS_FIELD, self.EXTERNAL_FIELD))
        if set(data) != expected:
            raise ValueError("Unsupported calendar storage fields")
        single_alarm = data[self.VERSION_FIELD] == self.SINGLE_ALARM_VERSION
        decode = CalendarEvent.from_single_alarm if single_alarm else CalendarEvent.from_dict
        events = tuple(decode(item) for item in data[self.EVENTS_FIELD])
        bindings = ActionCatalog()
        if data[self.VERSION_FIELD] == self.VERSION:
            actions = data[self.ACTIONS_FIELD]
            if not isinstance(actions, dict) or set(actions) != {event.id for event in events}:
                raise ValueError("Calendar action identifiers do not match schedules")
            events = tuple(ActionSettings.from_dict(actions[event.id]).apply(event) for event in events)
            bindings = ActionCatalog(tuple(ActionBinding.from_dict(item) for item in data[self.EXTERNAL_FIELD]))
        if len({item.id for item in events}) != len(events):
            raise ValueError("Duplicate calendar event identifiers")
        receipts = data[self.RECEIPTS_FIELD]
        commands = data[self.COMMANDS_FIELD] if data[self.VERSION_FIELD] >= self.COMBINED_VERSION else {}
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
        self._cached = text, snapshot, receipts.copy(), commands.copy(), bindings
        return snapshot, receipts, commands, bindings

    def _write(self, snapshot, receipts, commands, bindings):
        schedules, actions = [], {}
        for event in snapshot.events:
            schedule, settings = ActionSettings.split(event.to_dict())
            schedules.append(schedule)
            actions[event.id] = settings.to_dict()
        data = {self.VERSION_FIELD: self.VERSION, self.EVENTS_FIELD: schedules,
                self.ACTIONS_FIELD: actions, self.EXTERNAL_FIELD: [item.to_dict() for item in bindings.bindings],
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
            snapshot, receipts, commands, bindings = self._read()
            previous = snapshot.get(event.id)
            if (previous.revision if previous else 0) != event.revision:
                raise CalendarConflict("Event changed elsewhere. Reload before saving.")
            saved = replace(event, revision=event.revision + 1)
            result = CalendarSnapshot(tuple(item for item in snapshot.events if item.id != event.id) + (saved,))
            if previous:
                commands.pop(previous.command_key, None)
                for alarm in previous.alarms:
                    receipts.pop(previous.reminder_key(alarm), None)
            self._write(result, receipts, commands, bindings)
            return result, saved

    def delete(self, event: CalendarEvent):
        with self._locked():
            snapshot, receipts, commands, bindings = self._read()
            previous = snapshot.get(event.id)
            if previous is None or previous.revision != event.revision:
                raise CalendarConflict("Event changed elsewhere. Reload before deleting.")
            result = CalendarSnapshot(tuple(item for item in snapshot.events if item.id != event.id))
            commands.pop(previous.command_key, None)
            for alarm in previous.alarms:
                receipts.pop(previous.reminder_key(alarm), None)
            self._write(result, receipts, commands, bindings)
            return result

    def claim_due(self, now):
        """Claim due reminders; retain the existing event-oriented result API."""
        check = self.check(now)
        return check.snapshot, tuple(item.event for item in check.due)

    def load_actions(self):
        with self._locked():
            return self._read()[3]

    def save_actions(self, binding):
        with self._locked():
            snapshot, receipts, commands, catalog = self._read()
            previous = catalog.get(binding.link)
            if (previous.revision if previous else 0) != binding.revision:
                if previous is not None and previous.revision == binding.revision + 1 and previous.settings == binding.settings:
                    return catalog, previous  # A durable replace may have preceded a failed fsync acknowledgement.
                raise CalendarConflict("Execution settings changed elsewhere. Reload before saving.")
            saved = replace(binding, revision=binding.revision + 1)
            catalog = ActionCatalog(tuple(item for item in catalog.bindings if item.link.identity != binding.link.identity) + (saved,))
            self._write(snapshot, receipts, commands, catalog)
            return catalog, saved

    def delete_actions(self, link):
        with self._locked():
            snapshot, receipts, commands, catalog = self._read()
            catalog = ActionCatalog(tuple(item for item in catalog.bindings if item.link.identity != link.identity
                and not (item.link.source == link.source and item.link.series == link.event)))
            self._write(snapshot, receipts, commands, catalog)
            return catalog

    def reset_command_watch(self):
        self._scheduler.reset()

    def check(self, now, deliver=True, commands=False, external=(), expected_actions=ActionCatalog()):
        """Atomically admit actions before effects; planning is owned by the scheduler."""
        with self._locked():
            snapshot, receipts, command_receipts, catalog = self._read()
            if catalog != expected_actions:
                external = ()  # Re-resolve changed local bindings before admitting effects.
            plan = self._scheduler.plan(snapshot.events + tuple(external), now, receipts, command_receipts,
                                        deliver=deliver, commands=commands)
            if plan.reminders or plan.commands:
                self._write(snapshot, receipts, command_receipts, catalog)
            return CalendarCheck(snapshot, plan.reminders, plan.next_at, plan.commands, catalog)
