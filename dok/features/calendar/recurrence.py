"""Recurrence intervals and termination own parsing, bounds and indexing."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from ...core.records import Choice, Nullable, Number, Record, Rule, SettingsError, setting
from ...locales import Locale, Message as M


class CalendarDate(Rule):
    def decode(self, value):
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError as exc:
                raise SettingsError("Use YYYY-MM-DD") from exc
        if type(value) is not date:
            raise SettingsError("Expected a calendar date")
        return value

    def encode(self, value):
        return value.isoformat()

    def schema(self):
        return {"type": "string", "format": "date"}


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


class RepeatEnd(StrEnum):
    COUNT = "count"
    DATE = "date"

    def translated(self, locale):
        return locale.text(M.REPEAT_COUNT_MODE if self == self.COUNT else M.REPEAT_DATE_MODE)


@dataclass(frozen=True, slots=True)
class Recurrence(Record):
    unit: RepeatUnit = setting(Choice(RepeatUnit), default=RepeatUnit.NONE, description="Calendar recurrence unit.")
    interval: int = setting(Number(minimum=1, integer=True), default=1, description="Positive recurrence interval; months and years clamp to shorter months.")
    end: RepeatEnd = setting(Choice(RepeatEnd), default=RepeatEnd.COUNT, description="Stop after a total count or on an inclusive local start date.")
    count: int = setting(Number(minimum=0, integer=True), default=0, description="Total occurrences, including the first. Zero repeats indefinitely; used with count mode.")
    until: date | None = setting(Nullable(CalendarDate()), default=None, description="Inclusive final start date, required in date mode.")

    def _check(self):
        if self.unit == RepeatUnit.NONE and (self.end != RepeatEnd.COUNT or self.count or self.until is not None):
            raise SettingsError("A nonrepeating event cannot have repeat limits")
        if self.end == RepeatEnd.DATE and (self.until is None or self.count):
            raise SettingsError("Date mode requires an end date and a zero count")
        if self.end == RepeatEnd.COUNT and self.until is not None:
            raise SettingsError("Count mode cannot contain an end date")

    @classmethod
    def from_input(cls, unit, interval, end=RepeatEnd.COUNT, count="0", until=None):
        unit, end = RepeatUnit(unit), RepeatEnd(end)
        if unit == RepeatUnit.NONE:
            return cls(unit, int(interval))
        return cls(unit, int(interval), end, int(count) if end == RepeatEnd.COUNT else 0,
                   CalendarDate().decode(until) if end == RepeatEnd.DATE else None)

    @classmethod
    def count_display(cls, text: str, cursor: int, locale: Locale) -> tuple[str, int]:
        if not text:
            return "", 0
        try:
            count = cls.from_input(RepeatUnit.DAYS, 1, count=text).count
        except ValueError:
            return text, cursor
        if count == 0:
            label = locale.text(M.REPEAT_FOREVER)
            return label, len(label) if cursor else 0
        digits = str(count)
        position = max(0, min(len(digits), cursor - (len(text) - len(digits))))
        return locale.text(M.REPEAT_TIMES, count=count), position

    def _day(self, origin: date, index: int) -> date | None:
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

    def _index(self, origin: date, target: date) -> int:
        if self.unit == RepeatUnit.NONE:
            return 0
        if self.unit in (RepeatUnit.DAYS, RepeatUnit.WEEKS):
            distance = (target - origin).days
            step = self.interval * (7 if self.unit == RepeatUnit.WEEKS else 1)
        else:
            distance = (target.year - origin.year) * 12 + target.month - origin.month
            step = self.interval * (12 if self.unit == RepeatUnit.YEARS else 1)
        return max(0, distance // step)

    def day(self, origin: date, index: int) -> date | None:
        if self.end == RepeatEnd.COUNT and self.count and index >= self.count:
            return None
        day = self._day(origin, index)
        return None if day is not None and self.until is not None and day > self.until else day

    def index_near(self, origin: date, target: date) -> int:
        if self.until is not None:
            target = min(target, self.until)
        index = self._index(origin, target)
        if self.count:
            index = min(index, self.count - 1)
        while index and self.day(origin, index) is None:
            index -= 1
        return index

    def validate_start(self, origin: date) -> None:
        if self.unit != RepeatUnit.NONE and self.until is not None and self.until < origin:
            raise SettingsError("Repeat end date cannot precede event start")
