"""External calendar edit values, conditional mutations and conflict outcomes."""
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from ...locales import Message as M
from .contracts import CalendarSource, ExternalEvent, ExternalRecurrence


class EventField(StrEnum):
    TITLE = "title"
    START = "start"
    END = "end"
    LOCATION = "location"
    NOTES = "notes"
    REPEAT = "repeat"
    TIMEZONE = "timezone"

    def label(self, locale):
        return locale.text({self.TITLE: M.TITLE, self.START: M.START_DATE, self.END: M.END_DATE,
                            self.LOCATION: M.LOCATION, self.NOTES: M.NOTES, self.REPEAT: M.REPEAT,
                            self.TIMEZONE: M.TIME}[self])


@dataclass(frozen=True, slots=True)
class EventValues:
    title: str
    start: date | datetime
    end: date | datetime
    location: str = ""
    notes: str = ""
    repeat: ExternalRecurrence = ExternalRecurrence()
    timezone: str | None = None

    def __post_init__(self):
        # The read contract owns boundary and text validation for both paths.
        ExternalEvent("draft", self.title, self.start, self.end, self.location, self.notes,
                      repeat=self.repeat, timezone=self.timezone)

    @classmethod
    def from_event(cls, event: ExternalEvent):
        return cls(event.title, event.start, event.end, event.location, event.notes, event.repeat, event.timezone)

    def value(self, field: EventField):
        return getattr(self, field.value)

    def changes(self, original):
        def comparable(value):
            if isinstance(value, ExternalRecurrence) and value.rule is not None:
                return value.rule
            return value.astimezone(timezone.utc) if isinstance(value, datetime) else value
        return tuple(field for field in EventField
                     if comparable(self.value(field)) != comparable(original.value(field)))

    def rebase(self, original, latest):
        return replace(latest, **{field.value: self.value(field) for field in self.changes(original)})


class WriteKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class CalendarMutation:
    source: CalendarSource
    kind: WriteKind
    event_id: str
    values: EventValues | None = None
    original: ExternalEvent | None = None

    def __post_init__(self):
        object.__setattr__(self, "kind", WriteKind(self.kind))
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError("A mutation needs an event identifier")
        if self.kind == WriteKind.DELETE and self.values is not None:
            raise ValueError("Deletion cannot include replacement values")
        if self.kind != WriteKind.DELETE and self.values is None:
            raise ValueError("Saving needs event values")
        if self.kind == WriteKind.CREATE:
            if self.original is not None:
                raise ValueError("Creation cannot use an existing version")
        elif self.original is None or self.original.id != self.event_id or not self.original.editable:
            raise ValueError("Editing requires an editable original version")

    @staticmethod
    def new_id() -> str:
        # Stable across one draft's requests; also valid for Google's event ID alphabet.
        return uuid4().hex

    @property
    def fields(self):
        return (tuple(EventField) if self.kind == WriteKind.CREATE else () if self.values is None else
                self.values.changes(EventValues.from_event(self.original)))


class WriteProblem(StrEnum):
    DENIED = "denied"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"

    def message(self, locale):
        return locale.text({self.DENIED: M.CALENDAR_WRITE_DENIED, self.FAILED: M.CALENDAR_WRITE_FAILED,
                           self.UNCERTAIN: M.CALENDAR_WRITE_UNCERTAIN, self.UNAVAILABLE: M.CALENDAR_UNAVAILABLE}[self])


class CalendarWriteError(Exception):
    def __init__(self, problem: WriteProblem):
        self.problem = problem
        super().__init__(problem.value)


class CalendarConflict(Exception):
    def __init__(self, latest: ExternalEvent | None):
        self.latest = latest
        super().__init__("Calendar event version changed")


class CalendarWriter(Protocol):
    def can_write(self, source: CalendarSource) -> bool: ...
    async def get(self, source: CalendarSource, event_id: str) -> ExternalEvent | None: ...
    async def apply(self, mutation: CalendarMutation) -> ExternalEvent | None: ...
