"""External calendar read contract; writes are separate from local scheduling."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo
import calendar

from ...core.records import Boolean, POSITIVE_SECONDS, Record, Text, setting
from .recurrence import Recurrence, RepeatUnit


@dataclass(frozen=True, slots=True)
class ExternalRecurrence:
    """Editable common rule, or an opaque provider rule preserved without changes."""
    rule: Recurrence | None = Recurrence()
    encoded: tuple[str, ...] = ()

    @property
    def editable(self):
        return self.rule is not None

    @property
    def repeating(self):
        return bool(self.encoded) if self.rule is None else self.rule.unit != RepeatUnit.NONE


@dataclass(frozen=True, slots=True)
class CalendarSource(Record):
    connection: str = setting(Text(nonblank=True, single_line=True),
        description="Configured connection identifier. The selected connection must support calendar reads.")
    calendar: str = setting(Text(nonblank=True, single_line=True),
        description="Provider calendar identifier. Google accepts primary for the account's main calendar, or a calendar ID from its settings.")
    label: str = setting(Text(nonblank=True, single_line=True),
        description="Source name shown next to external events. Distinguish accounts/calendars here.")
    enabled: bool = setting(Boolean(), default=False,
        description="Enable this calendar source and its explicitly configured dok actions. Editing additionally requires provider write settings and calendar permissions.")
    refresh_s: float = setting(POSITIVE_SECONDS, default=300,
        description="Refresh interval for the visible month and schedules with dok reminders/commands. Hidden popups read only action-linked schedules. F5 explicitly refreshes.")

    @property
    def identity(self):
        return self.connection, self.calendar


@dataclass(frozen=True, slots=True)
class CalendarWindow:
    year: int
    month: int
    timezone: str | None

    def __post_init__(self):
        date(self.year, self.month, 1)
        if self.timezone is not None:
            ZoneInfo(self.timezone)

    def utc_bounds(self):
        first = datetime(self.year, self.month, 1)
        if (self.year, self.month) == (date.max.year, 12):
            last = datetime.max.replace(microsecond=0)
        else:
            last = (first + timedelta(days=calendar.monthrange(self.year, self.month)[1]))
        def utc(value):
            localized = value.replace(tzinfo=ZoneInfo(self.timezone)) if self.timezone else value.astimezone()
            return localized.astimezone(timezone.utc).isoformat()
        return utc(first), utc(last)


@dataclass(frozen=True, slots=True)
class ExternalEvent:
    id: str
    title: str
    start: date | datetime
    end: date | datetime
    location: str = ""
    notes: str = ""
    url: str = ""
    version: str = ""
    recurring: bool = False
    locked: bool = False
    series_id: str = ""
    original_start: date | datetime | None = None
    repeat: ExternalRecurrence = ExternalRecurrence()
    timezone: str | None = None

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("External event requires an identifier")
        if type(self.start) not in (date, datetime) or type(self.end) is not type(self.start):
            raise ValueError("External event boundaries must have the same date/time type")
        if isinstance(self.start, datetime) and any(value.utcoffset() is None for value in (self.start, self.end)):
            raise ValueError("External event timestamps require an offset")
        start, end = ((self.start.astimezone(timezone.utc), self.end.astimezone(timezone.utc))
                      if isinstance(self.start, datetime) else (self.start, self.end))
        if end <= start:
            raise ValueError("External event end must be after start")
        if any(not isinstance(value, str) for value in (self.title, self.location, self.notes, self.url)):
            raise ValueError("External event text must be strings")
        if not isinstance(self.version, str) or type(self.recurring) is not bool or type(self.locked) is not bool:
            raise ValueError("Invalid external event edit metadata")
        if not isinstance(self.series_id, str) or not isinstance(self.repeat, ExternalRecurrence):
            raise ValueError("Invalid recurring event metadata")
        if self.timezone is not None:
            ZoneInfo(self.timezone)
        if self.original_start is not None and type(self.original_start) not in (date, datetime):
            raise ValueError("Occurrence identity requires a date or timestamp")
        if isinstance(self.original_start, datetime) and self.original_start.utcoffset() is None:
            raise ValueError("Occurrence identity timestamp requires an offset")

    def project(self, source, zone):
        all_day = type(self.start) is date
        def local(value):
            return (datetime.combine(value, time()) if all_day else
                    value.astimezone(ZoneInfo(zone) if zone else None))
        return CalendarEntry((*source.identity, self.id), source.label, self.title, local(self.start), local(self.end),
                             all_day, self.location, self.notes, self.url)

    @property
    def editable(self) -> bool:
        return bool(self.version) and not self.locked

    @property
    def is_series(self):
        return self.recurring and not self.series_id


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    id: tuple[str, str, str]
    source: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    location: str
    notes: str
    url: str
    holiday: bool = False

    @property
    def caption(self):
        return "↗ " + self.title

    def on_day(self, day):
        return self.start.date() <= day <= (self.end - timedelta(microseconds=1)).date()


class CalendarProvider(Protocol):
    async def fetch(self, source: CalendarSource, window: CalendarWindow) -> tuple[ExternalEvent, ...]: ...
    async def get(self, source: CalendarSource, event_id: str) -> ExternalEvent | None: ...
    async def instances(self, source: CalendarSource, event_id: str, lower: datetime, upper: datetime) -> tuple[ExternalEvent, ...]: ...
