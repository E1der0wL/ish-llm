"""Date, time and FIGlet setting declarations and interpretation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones
import re

from pyfiglet import Figlet, FigletFont

from ...core.elements import ElementConfig, Spacing
from ...core.records import Boolean, Nested, Nullable, Record, Rule, SettingsError, Text, setting
from ...core.styles import TextStyle, BoldStyle
from ...rendering.presentation import TextRow
from ...locales import Locale, Message
from ...locales.messages import MONTHS


class TimezoneRule(Rule):
    def __init__(self):
        self._zones = frozenset(available_timezones())

    def decode(self, value):
        Text(single_line=True).decode(value)
        if value not in self._zones:
            raise SettingsError("Unknown IANA timezone")
        return value

    def schema(self):
        return {"type": "string", "enum": sorted(self._zones)}


class ClockFormat(Rule):
    DIRECTIVES = "YymdAaBbHIMSpzZ%"
    PATTERN = rf"^(?:[^%\x00-\x1f\x7f-\x9f\u2028\u2029]|%[{DIRECTIVES}])*$"
    MONTHS = tuple(month.upper() for month in MONTHS)

    def decode(self, value):
        Text(single_line=True).decode(value)
        if re.fullmatch(self.PATTERN, value) is None:
            raise SettingsError(f"Supported time directives: {self.DIRECTIVES}")
        return value

    def schema(self):
        return {"type": "string", "pattern": self.PATTERN}

    @classmethod
    def render(cls, fmt, now, *, weekdays: tuple[str, ...] | None = None, locale=Locale()):
        def token(match):
            value = match.group()
            if value in ("%B", "%b"):
                month = locale.month(now.month).upper()
                return month if value == "%B" else month[:3]
            if value in ("%a", "%A"):
                return weekdays[now.weekday()] if weekdays is not None else locale.weekday(now.weekday(), abbreviated=value == "%a")
            if value == "%p":
                return locale.text(Message.AM if now.hour < 12 else Message.PM)
            return now.strftime(value)
        return re.sub(r"%.", token, fmt)


@dataclass(frozen=True, slots=True)
class ClockConfig(Record):
    timezone: str | None = setting(
        Nullable(TimezoneRule()),
        default=None,
        description="Shared IANA timezone for date, time, weekday art and weather timestamps. null uses the system local timezone."
    )

    def now(self):
        return datetime.now(ZoneInfo(self.timezone)) if self.timezone is not None else datetime.now().astimezone()

    def next_tick(self, now, date, time):
        return self.schedule(date, time).next_tick(now)

    def schedule(self, date, time) -> ClockSchedule:
        return ClockSchedule.for_formats(self, *(element.format for element in (date, time) if element.needs_clock))


@dataclass(frozen=True, slots=True)
class ClockSchedule:
    """Effective clock inputs; styling and same-cadence formats are excluded."""

    clock: ClockConfig
    seconds: bool

    @classmethod
    def for_formats(cls, clock: ClockConfig, *formats: str) -> ClockSchedule:
        """Own cadence interpretation for every consumer of clock formats."""
        return cls(clock, any("%S" in fmt.replace("%%", "") for fmt in formats))

    def next_tick(self, now: datetime) -> float:
        return (1 if self.seconds else 60 - now.second) - now.microsecond / 1_000_000

@dataclass(frozen=True, slots=True)
class DateStyle(BoldStyle):
    bold: bool = setting(Boolean(), default=True)


@dataclass(frozen=True, slots=True)
class DateConfig(ElementConfig):
    style: DateStyle = setting(
        Nested(DateStyle),
        factory=DateStyle,
        description="Date foreground color and bold text."
    )
    format: str = setting(
        ClockFormat(),
        default="%d %B, %Y.",
        description="Date format. %d is day, %B localized month (uppercase in English), %Y year. Empty hides this row."
    )

    @property
    def needs_clock(self) -> bool:
        return self.enabled and bool(self.format)

    def rows(self, now, locale=Locale()):
        return (
            TextRow.plain(ClockFormat.render(self.format, now, locale=locale), self.style.color, bold=self.style.bold),
        ) if self.needs_clock else ()

@dataclass(frozen=True, slots=True)
class TimeSpacing(Spacing):
    after: int = setting(Spacing.ROWS_RULE, default=2)


@dataclass(frozen=True, slots=True)
class TimeConfig(ElementConfig):
    spacing: TimeSpacing = setting(Nested(TimeSpacing), factory=TimeSpacing)
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Time foreground color.")
    format: str = setting(
        ClockFormat(),
        default="-%H:%M-",
        description="Time format. %H is hour, %M minute, %S second. %S enables second updates; empty hides this row."
    )

    @property
    def needs_clock(self) -> bool:
        return self.enabled and bool(self.format)

    def rows(self, now, locale=Locale()):
        return (
            TextRow.plain(ClockFormat.render(self.format, now, locale=locale), self.style.color),
        ) if self.needs_clock else ()


class FontRule(Rule):
    def __init__(self):
        self._fonts = frozenset(FigletFont.getFonts())

    def decode(self, value):
        Text(nonblank=True, single_line=True).decode(value)
        if value not in self._fonts:
            raise SettingsError("Unknown pyfiglet font")
        try:
            Figlet(font=value)
        except Exception as exc:
            raise SettingsError(f"Cannot load pyfiglet font: {value}") from exc
        return value

    def schema(self):
        return {"type": "string", "enum": sorted(self._fonts)}


@dataclass(frozen=True, slots=True)
class WeekdaySpacing(Spacing):
    after: int = setting(Spacing.ROWS_RULE, default=1)


@dataclass(frozen=True, slots=True)
class WeekdayConfig(ElementConfig):
    spacing: WeekdaySpacing = setting(Nested(WeekdaySpacing), factory=WeekdaySpacing)
    font: str = setting(
        FontRule(),
        default="future",
        description="Installed pyfiglet font name. Examples: future, slant, ansi_regular, small."
    )
    style: TextStyle = setting(
        Nested(TextStyle),
        factory=TextStyle,
        description="Weekday art foreground color."
    )
    abbreviated: bool = setting(
        Boolean(),
        default=False,
        description="Use abbreviated English weekdays. Automatically falls back to abbreviations or plain text when space is limited."
    )
