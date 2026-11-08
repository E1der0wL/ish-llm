"""Typed start-time command occurrences and submission outcomes."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .models import CalendarEvent
from .reminders import Reminder, ReminderUnit


class CommandResult(StrEnum):
    SUBMITTED = "submitted"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CommandOccurrence:
    event: CalendarEvent
    at: datetime
    AT_START = Reminder(ReminderUnit.START)

    @property
    def stamp(self) -> str:
        return self.event.start.isoformat()

    @classmethod
    def latest(cls, event: CalendarEvent, now: datetime):
        occurrence = event.latest_due(now, cls.AT_START)
        return cls(occurrence.event, occurrence.at) if occurrence is not None else None

    @classmethod
    def next_at(cls, event: CalendarEvent, now: datetime):
        return event.next_due(now, cls.AT_START)
