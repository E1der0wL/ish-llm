"""Weather location, formatting and refresh settings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import sys
from typing import ClassVar

from ...core.elements import ElementConfig, Spacing
from ...core.records import Choice, Nested, Nullable, Number, Record, Sequence, Text, setting
from ...core.styles import TextStyle
from ...core.theme import ThemeRole, theme_reference
from .models import TemperatureUnit, WeatherKind
from ...locales import Locale, Language


@dataclass(frozen=True, slots=True)
class WeatherIcons(Record):
    clear: str = setting(Text(single_line=True), default="☀", description="Clear sky icon.")
    partly_cloudy: str = setting(Text(single_line=True), default="🌤", description="Partly cloudy icon.")
    cloudy: str = setting(Text(single_line=True), default="☁", description="Cloudy icon.")
    fog: str = setting(Text(single_line=True), default="🌫", description="Fog icon.")
    rain: str = setting(Text(single_line=True), default="🌧", description="Rain icon.")
    snow: str = setting(Text(single_line=True), default="❄", description="Snow icon.")
    thunder: str = setting(Text(single_line=True), default="⛈", description="Thunderstorm icon.")
    unknown: str = setting(
        Text(single_line=True), default="?",
        description="Unknown weather icon. Also used when no forecast location is configured."
    )

    def for_kind(self, kind):
        return getattr(self, WeatherKind(kind).value)


@dataclass(frozen=True, slots=True)
class WeatherLocation(Record):
    lat: float = setting(
        Number(minimum=-90, maximum=90),
        description="Latitude of the forecast location."
    )
    lon: float = setting(
        Number(minimum=-180, maximum=180),
        description="Longitude of the forecast location."
    )
    label: str = setting(
        Text(single_line=True),
        default="",
        description="Location label shown in weather details. Not used to look up coordinates."
    )

    @property
    def coordinates(self) -> tuple[float, float]:
        return self.lat, self.lon


@dataclass(frozen=True, slots=True)
class WeatherStyle(TextStyle):
    low_color: str = theme_reference(ThemeRole.LOW_TEMP, "Low temperature foreground")

@dataclass(frozen=True, slots=True)
class WeatherSpacing(Spacing):
    after: int = setting(Spacing.ROWS_RULE, default=1)


@dataclass(frozen=True, slots=True)
class WeatherConfig(ElementConfig):
    FORECAST_DAYS: ClassVar[Number] = Number(minimum=1, maximum=16, integer=True)
    WEEKDAY_LABELS: ClassVar[tuple[str, ...]] = ("ᴍᴏɴ", "ᴛᴜᴇ", "ᴡᴇᴅ", "ᴛʜᴜ", "ꜰʀɪ", "ꜱᴀᴛ", "ꜱᴜɴ")
    spacing: WeatherSpacing = setting(Nested(WeatherSpacing), factory=WeatherSpacing)
    location: WeatherLocation | None = setting(Nullable(Nested(WeatherLocation)), default=None,
        description="Forecast location. null disables weather requests. Supply your own lat and lon.\nObject format: {\"lat\": <lat>, \"lon\": <lon>, \"label\": \"<location name>\"}")
    unit: TemperatureUnit = setting(
        Choice(TemperatureUnit),
        default=TemperatureUnit.CELSIUS,
        description="Temperature unit: celsius or fahrenheit."
    )
    days: int = setting(
        FORECAST_DAYS,
        default=7,
        description=f"Forecast days to display, from 1 to the Open-Meteo maximum of {FORECAST_DAYS.maximum}. Fewer days are shown when space is limited."
    )
    refresh_min: int = setting(
        Number(minimum=1, integer=True),
        default=30,
        description="Positive whole minutes between scheduled weather requests. Successful forecasts keep this cadence when the dock reopens. After a failed request, explicitly reopening the dock retries immediately; an in-flight request is never duplicated."
    )
    style: WeatherStyle = setting(
        Nested(WeatherStyle),
        factory=WeatherStyle,
        description="Weather foreground and low-temperature colors."
    )
    width_ratio: float = setting(
        Number(minimum=0, maximum=1, exclusive_minimum=True),
        default=0.5,
        description="Target weather width as a fraction of the dock width. Enough space is retained to prevent temperature overlap."
    )
    icon_offset: int = setting(
        Number(integer=True),
        default=-1,
        description="Horizontal icon offset in cells. Negative moves left; positive moves right. Clamped within each column."
    )
    icons: WeatherIcons = setting(
        Nested(WeatherIcons),
        factory=WeatherIcons,
        description="Icons or single-line text for each weather condition."
    )
    weekdays: tuple[str, ...] | None = setting(
        Nullable(Sequence(
            Text(nonblank=True, single_line=True),
            minimum=len(WEEKDAY_LABELS),
            maximum=len(WEEKDAY_LABELS)
        )),
        default=None,
        description="null uses localized weekdays (English uses small capitals). An array overrides Monday through Sunday; custom labels are preserved.",
    )

    def weekday_label(self, day: date, locale=Locale()) -> str:
        if self.weekdays is not None:
            return self.weekdays[day.weekday()]
        return self.WEEKDAY_LABELS[day.weekday()] if locale.language == Language.ENGLISH else locale.weekday(day.weekday(), abbreviated=True)

    @property
    def can_fetch(self):
        return self.enabled and self.location is not None

    @property
    def needs_clock(self) -> bool:
        return self.enabled and self.location is None

    @property
    def refresh_s(self):
        return min(self.refresh_min * 60, sys.float_info.max)

    @property
    def request_key(self):
        return (self.location.coordinates if self.location is not None else None, self.unit, self.days)

    @property
    def polling_key(self):
        """Only query identity and cadence affect the collection task."""
        return self.request_key, self.refresh_s
