"""Date/time selection rules, independent of terminal rendering and storage."""
import calendar
from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from typing import ClassVar

from ...locales import Message as M


@dataclass(slots=True)
class DateSelection:
    value: date
    week_start: int
    MONTHS_PER_YEAR: ClassVar[int] = 12
    DAYS_PER_WEEK: ClassVar[int] = 7

    @property
    def weekdays(self) -> tuple[int, ...]:
        return tuple(calendar.Calendar(self.week_start).iterweekdays())

    @property
    def weeks(self) -> tuple[tuple[date | None, ...], ...]:
        return tuple(tuple(date(self.value.year, self.value.month, day) if day else None for day in week)
                     for week in calendar.Calendar(self.week_start).monthdayscalendar(self.value.year, self.value.month))

    def select(self, value: date) -> None:
        if type(value) is not date:
            raise ValueError("Expected a date")
        self.value = value

    def days(self, amount: int) -> None:
        self.value = date.fromordinal(max(date.min.toordinal(), min(date.max.toordinal(), self.value.toordinal() + amount)))

    def months(self, amount: int) -> None:
        index = (self.value.year - 1) * self.MONTHS_PER_YEAR + self.value.month - 1 + amount
        index = max(0, min(date.max.year * self.MONTHS_PER_YEAR - 1, index))
        year, month = index // self.MONTHS_PER_YEAR + 1, index % self.MONTHS_PER_YEAR + 1
        self.value = date(year, month, min(self.value.day, calendar.monthrange(year, month)[1]))

    def years(self, amount: int) -> None:
        self.months(amount * self.MONTHS_PER_YEAR)

    def last_day(self) -> None:
        self.value = self.value.replace(day=calendar.monthrange(self.value.year, self.value.month)[1])


class TimePart(StrEnum):
    HOUR = "hour"
    MINUTE = "minute"
    SECOND = "second"

    @property
    def minimum(self) -> int:
        return getattr(time.min, self.value)

    @property
    def maximum(self) -> int:
        return getattr(time.max, self.value)

    def translated(self, locale) -> str:
        return locale.text({self.HOUR: M.HOUR, self.MINUTE: M.MINUTE, self.SECOND: M.SECOND}[self])


@dataclass(slots=True)
class TimeSelection:
    value: time

    def current(self, part: TimePart) -> int:
        return getattr(self.value, part.value)

    def select(self, part: TimePart, value: int) -> None:
        # datetime.time owns valid clock ranges; preserve the existing fraction.
        self.value = self.value.replace(**{part.value: value})

    def step(self, part: TimePart, amount: int) -> None:
        self.select(part, max(part.minimum, min(part.maximum, self.current(part) + amount)))

    def window(self, part: TimePart, count: int) -> range:
        count = max(1, min(part.maximum - part.minimum + 1, count))
        start = max(part.minimum, min(self.current(part) - count // 2, part.maximum + 1 - count))
        return range(start, start + count)
