"""Reminder offsets own units, input parsing, labels and duplicate semantics."""
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from ...core.records import Choice, Number, Record, SettingsError, setting
from ...locales import Locale, Message as M


class ReminderUnit(StrEnum):
    START = "start"
    WEEKS = "weeks"
    DAYS = "days"
    HOURS = "hours"
    MINUTES = "minutes"
    SECONDS = "seconds"

    @property
    def seconds(self):
        return {self.START: 0, self.WEEKS: 604800, self.DAYS: 86400,
                self.HOURS: 3600, self.MINUTES: 60, self.SECONDS: 1}[self]

    @property
    def uses_number(self):
        return self != self.START

    def translated(self, locale):
        return locale.text({self.START: M.AT_EVENT_START, self.WEEKS: M.WEEKS_BEFORE,
                            self.DAYS: M.DAYS_BEFORE, self.HOURS: M.HOURS_BEFORE,
                            self.MINUTES: M.MINUTES_BEFORE, self.SECONDS: M.SECONDS_BEFORE}[self])


@dataclass(frozen=True, slots=True)
class Reminder(Record):
    unit: ReminderUnit = setting(Choice(ReminderUnit), default=ReminderUnit.MINUTES,
                                description="Offset unit; start ignores the number.")
    value: int | float = setting(Number(), default=5,
                                description="Amount before event start. Negative values retain reminders after start; fractional values are supported.")

    def _check(self):
        if not self.unit.uses_number:
            object.__setattr__(self, "value", 0)
        try:
            self.delta
        except OverflowError as exc:
            raise SettingsError("Reminder offset exceeds the supported date range") from exc

    @property
    def delta(self):
        return timedelta(seconds=self.value * self.unit.seconds)

    @property
    def identity(self):
        delta = self.delta
        return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds

    @classmethod
    def from_input(cls, unit, text):
        unit = ReminderUnit(unit)
        if not unit.uses_number:
            return cls(unit, 0)
        try:
            value = float(text)
        except ValueError as exc:
            raise SettingsError("Enter a reminder number") from exc
        return cls(unit, value)

    @classmethod
    def from_delta(cls, delta):
        seconds = delta.total_seconds()
        if not seconds:
            return cls(ReminderUnit.START, 0)
        for unit in ReminderUnit:
            if unit.uses_number and seconds % unit.seconds == 0:
                return cls(unit, seconds / unit.seconds)
        return cls(ReminderUnit.SECONDS, seconds)

    @staticmethod
    def ordered(reminders):
        # The same deadline expressed in different units is one reminder.
        return tuple(sorted({item.identity: item for item in reversed(reminders)}.values(),
                            key=lambda item: item.identity, reverse=True))

    def label(self, locale=Locale()):
        if self.identity == 0:
            return ReminderUnit.START.translated(locale)
        return f"{self.value:g} {self.unit.translated(locale)}"
