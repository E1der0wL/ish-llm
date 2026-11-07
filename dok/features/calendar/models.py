"""Calendar records own validation, recurrence and reminder interpretation."""
from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import os
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from ...core.records import Boolean, Choice, Nested, Nullable, Number, Record, Rule, SettingsError, Text, setting
from ..clock.config import TimezoneRule
from ...locales import Locale, Message as M


class LocalDateTime(Rule):
    def decode(self, value):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as exc:
                raise SettingsError("Use YYYY-MM-DD HH:MM") from exc
        if not isinstance(value, datetime) or value.tzinfo is not None:
            raise SettingsError("Expected a local date and time without a UTC offset")
        return value

    def encode(self, value):
        return value.isoformat()

    @staticmethod
    def edit_text(value):
        return value.isoformat(sep=" ", timespec="auto" if value.second or value.microsecond else "minutes")

    def schema(self):
        return {"type": "string", "format": "local-date-time"}


class RepeatUnit(StrEnum):
    NONE = "none"
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"

    @property
    def label(self):
        return self.translated(Locale())

    def translated(self, locale):
        return locale.text({self.NONE: M.NO_REPEAT, self.DAYS: M.DAYS, self.WEEKS: M.WEEKS,
                           self.MONTHS: M.MONTHS, self.YEARS: M.YEARS}[self])


@dataclass(frozen=True, slots=True)
class Recurrence(Record):
    unit: RepeatUnit = setting(Choice(RepeatUnit), default=RepeatUnit.NONE, description="Calendar recurrence unit.")
    interval: int = setting(Number(minimum=1, integer=True), default=1, description="Positive recurrence interval; months and years clamp to the last day of shorter months.")

    @classmethod
    def from_input(cls, unit, interval):
        return cls(unit, int(interval))

    def day(self, origin: date, index: int) -> date | None:
        if index < 0 or self.unit == RepeatUnit.NONE and index:
            return None
        try:
            if self.unit in (RepeatUnit.NONE, RepeatUnit.DAYS, RepeatUnit.WEEKS):
                return origin + timedelta(days=index * self.interval * (7 if self.unit == RepeatUnit.WEEKS else 1))
            months = index * self.interval * (12 if self.unit == RepeatUnit.YEARS else 1)
            total = (origin.year - 1) * 12 + origin.month - 1 + months
            year, month = total // 12 + 1, total % 12 + 1
            return date(year, month, min(origin.day, monthrange(year, month)[1]))
        except (ValueError, OverflowError):
            return None

    def index_near(self, origin: date, target: date) -> int:
        if self.unit == RepeatUnit.NONE:
            return 0
        if self.unit in (RepeatUnit.DAYS, RepeatUnit.WEEKS):
            distance = (target - origin).days
            step = self.interval * (7 if self.unit == RepeatUnit.WEEKS else 1)
        else:
            distance = (target.year - origin.year) * 12 + target.month - origin.month
            step = self.interval * (12 if self.unit == RepeatUnit.YEARS else 1)
        return max(0, distance // step)


@dataclass(frozen=True, slots=True)
class CalendarEvent(Record):
    title: str = setting(Text(nonblank=True, single_line=True), description="Event title.")
    start: datetime = setting(LocalDateTime(), description="Local start date and time.")
    end: datetime = setting(LocalDateTime(), description="Local end date and time, after start. All-day end is exclusive.")
    id: str = setting(Text(nonblank=True, single_line=True), factory=lambda: uuid4().hex, description="Stable event identifier.")
    revision: int = setting(Number(minimum=0, integer=True), default=0, description="Optimistic storage revision; zero means a new event.")
    timezone: str | None = setting(Nullable(TimezoneRule()), default=None, description="IANA timezone; null uses system local rules at the event date. Ambiguous times use the first occurrence.")
    all_day: bool = setting(Boolean(), default=False, description="All-day event, with midnight boundaries.")
    location: str = setting(Text(single_line=True), default="", description="Event location.")
    alarm: datetime | None = setting(Nullable(LocalDateTime()), default=None, description="Local reminder time for the first occurrence; subsequent reminders keep the same offset from start.")
    repeat: Recurrence = setting(Nested(Recurrence), factory=Recurrence, description="Repeating schedule.")
    notes: str = setting(Text(), default="", description="Multiline event notes.")
    holiday: bool = setting(Boolean(), default=False, description="Mark this user-registered event's dates as holidays.")

    def localize(self, value: datetime):
        aware = value.replace(tzinfo=ZoneInfo(self.timezone)) if self.timezone else value.astimezone()
        # Roundtrip rejects nonexistent wall times instead of silently shifting them.
        back = aware.astimezone(timezone.utc).astimezone(ZoneInfo(self.timezone) if self.timezone else None)
        if back.replace(tzinfo=None) != value:
            raise SettingsError("This local time does not exist because of a daylight-saving transition")
        return aware

    def _check(self):
        if self.end <= self.start:
            raise SettingsError("End must be after start")
        if self.all_day and (self.start.time() != datetime.min.time() or self.end.time() != datetime.min.time()):
            raise SettingsError("All-day events need midnight start and exclusive end")
        for value in (self.start, self.end, self.alarm):
            if value is not None:
                self.localize(value)

    @staticmethod
    def recurrence_context() -> tuple[str | None, tuple[str, str], int, int]:
        """System-local interpretation context for cached recurrence queries."""
        return os.environ.get("TZ"), time.tzname, time.timezone, time.daylight

    def occurrence(self, index: int):
        day = self.repeat.day(self.start.date(), index)
        if day is None:
            return None
        try:
            start = datetime.combine(day, self.start.time())
            delta = start - self.start
            return replace(self, start=start, end=self.end + delta,
                           alarm=self.alarm + delta if self.alarm else None)
        except (OverflowError, ValueError):
            return None

    def on_day(self, day: date):
        # One entry per series/day, including multi-day and overlapping series.
        index = self.repeat.index_near(self.start.date(), day)
        for current in (index, index - 1):
            event = self.occurrence(current)
            if event and event.start.date() <= day <= (event.end - timedelta(microseconds=1)).date():
                yield event
                return

    def latest_due(self, now: datetime):
        if self.alarm is None:
            return None
        local = now.astimezone(ZoneInfo(self.timezone) if self.timezone else None).replace(tzinfo=None)
        try:
            target = local + (self.start - self.alarm)
        except OverflowError:
            target = datetime.max
        index = self.repeat.index_near(self.start.date(), target.date())
        for current in (index + 1, index, index - 1):
            event = self.occurrence(current)
            if event and event.alarm and event.localize(event.alarm) <= now:
                return event
        return None

    @property
    def reminder_key(self):
        return f"{self.id}:{self.revision}"
