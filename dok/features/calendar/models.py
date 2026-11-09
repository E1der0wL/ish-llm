"""Calendar records own validation, recurrence and reminder interpretation."""
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import os
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from ...core.records import Boolean, Nested, Nullable, Number, Record, Rule, Sequence, SettingsError, Text, setting
from ..clock.config import TimezoneRule
from .reminders import Reminder
from .recurrence import Recurrence, RepeatUnit
from .actions import ActionSettings


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


@dataclass(frozen=True, slots=True)
class CalendarEvent(ActionSettings):
    title: str = setting(Text(nonblank=True, single_line=True), description="Event title.")
    start: datetime = setting(LocalDateTime(), description="Local start date and time.")
    end: datetime = setting(LocalDateTime(), description="Local end date and time, after start. All-day end is exclusive.")
    id: str = setting(Text(nonblank=True, single_line=True), factory=lambda: uuid4().hex, description="Stable event identifier.")
    revision: int = setting(Number(minimum=0, integer=True), default=0, description="Optimistic storage revision; zero means a new event.")
    timezone: str | None = setting(Nullable(TimezoneRule()), default=None, description="IANA timezone; null uses system local rules at the event date. Ambiguous times use the first occurrence.")
    all_day: bool = setting(Boolean(), default=False, description="All-day event, with midnight boundaries.")
    location: str = setting(Text(single_line=True), default="", description="Event location.")
    repeat: Recurrence = setting(Nested(Recurrence), factory=Recurrence, description="Repeating schedule.")
    notes: str = setting(Text(), default="", description="Multiline event notes.")
    holiday: bool = setting(Boolean(), default=False, description="Mark this user-registered event's dates as holidays.")

    @property
    def delivery_once(self):
        return False

    def localize(self, value: datetime):
        aware = value.replace(tzinfo=ZoneInfo(self.timezone)) if self.timezone else value.astimezone()
        # Roundtrip rejects nonexistent wall times instead of silently shifting them.
        back = aware.astimezone(timezone.utc).astimezone(ZoneInfo(self.timezone) if self.timezone else None)
        if back.replace(tzinfo=None) != value:
            raise SettingsError("This local time does not exist because of a daylight-saving transition")
        return aware

    def _check(self):
        self.repeat.validate_start(self.start.date())
        if self.end <= self.start:
            raise SettingsError("End must be after start")
        if self.all_day and (self.start.time() != datetime.min.time() or self.end.time() != datetime.min.time()):
            raise SettingsError("All-day events need midnight start and exclusive end")
        ActionSettings._check(self)
        for value in (self.start, self.end):
            self.localize(value)
        for alarm in self.alarms:
            try:
                self.localize(self.start - alarm.delta)
            except OverflowError as exc:
                raise SettingsError("Reminder falls outside the supported date range") from exc

    @classmethod
    def from_single_alarm(cls, data):
        """Decode the previous stored event format in its owning domain only."""
        if not isinstance(data, dict):
            raise SettingsError("Expected a calendar event object")
        values = dict(data)
        if "alarms" in values:
            raise SettingsError("The single-alarm storage format cannot contain an alarms list")
        alarm = values.pop("alarm", None)
        event = cls.from_dict(values)
        return replace(event, alarms=(Reminder.from_delta(event.start - LocalDateTime().decode(alarm)),)) if alarm is not None else event

    @staticmethod
    def recurrence_context() -> tuple[str | None, tuple[str, str], int, int]:
        """System-local interpretation context for cached recurrence queries."""
        return os.environ.get("TZ"), time.tzname, time.timezone, time.daylight

    def _bounds(self, index: int):
        day = self.repeat.day(self.start.date(), index)
        if day is None:
            return None
        try:
            start = datetime.combine(day, self.start.time())
            return start, self.end + (start - self.start)
        except OverflowError:
            return None

    def occurrence(self, index: int):
        bounds = self._bounds(index)
        if bounds is None:
            return None
        try:
            event = replace(self, start=bounds[0], end=bounds[1], alarms=())
            # A missing DST wall time skips that reminder, not the other offsets.
            alarms = tuple(alarm for alarm in self.alarms if ReminderOccurrence.for_event(event, alarm) is not None)
            return replace(event, alarms=alarms)
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

    def _reminder_index(self, alarm, now):
        local = now.astimezone(ZoneInfo(self.timezone) if self.timezone else None).replace(tzinfo=None)
        try:
            target = local + alarm.delta
        except OverflowError:
            target = datetime.max if alarm.identity >= 0 else datetime.min
        return self.repeat.index_near(self.start.date(), target.date())

    def latest_due(self, now: datetime, alarm: Reminder):
        now = now.astimezone(timezone.utc)
        index = self._reminder_index(alarm, now)
        for current in (index + 1, index, index - 1):
            event = self.occurrence(current)
            if event:
                occurrence = ReminderOccurrence.for_event(event, alarm)
                if occurrence and occurrence.at <= now:
                    return occurrence
        return None

    def next_due(self, now: datetime, alarm: Reminder):
        now = now.astimezone(timezone.utc)
        index = max(0, self._reminder_index(alarm, now) - 1)
        while (bounds := self._bounds(index)) is not None:
            try:
                bounds[0] - alarm.delta
            except OverflowError:
                # The first occurrence was validated. Later dates can only
                # overflow the upper bound; advancing cannot recover from it.
                return None
            event = self.occurrence(index)
            occurrence = ReminderOccurrence.for_event(event, alarm) if event else None
            if occurrence and occurrence.at > now:
                return occurrence.at
            index += 1
        return None

    @property
    def reminder_series(self):
        return f"{self.id}:{self.revision}"

    def reminder_key(self, alarm: Reminder):
        return f"{self.reminder_series}:{alarm.identity}"

    @property
    def command_key(self):
        return self.reminder_series


@dataclass(frozen=True, slots=True)
class ReminderOccurrence:
    event: CalendarEvent
    alarm: Reminder
    at: datetime

    @classmethod
    def for_event(cls, event, alarm):
        try:
            return cls(event, alarm, event.localize(event.start - alarm.delta))
        except (ValueError, OverflowError):
            return None

    @property
    def stamp(self):
        return (self.event.start - self.alarm.delta).isoformat()
