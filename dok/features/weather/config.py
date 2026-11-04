"""Weather location, formatting and refresh settings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from ...core.colors import DockPalette, HexColor
from ...core.elements import ElementConfig, Spacing
from ...core.records import Choice, Nested, Nullable, Number, Record, Sequence, Text, setting
from ...core.styles import TextStyle
from .models import TemperatureUnit, WeatherKind


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
    latitude: float = setting(
        Number(minimum=-90, maximum=90),
        description="Latitude of the forecast location."
    )
    longitude: float = setting(
        Number(minimum=-180, maximum=180),
        description="Longitude of the forecast location."
    )
    label: str = setting(
        Text(single_line=True),
        default="",
        description="Location label for your reference. Not displayed or used to look up coordinates."
    )


@dataclass(frozen=True, slots=True)
class WeatherStyle(TextStyle):
    low_temperature_color: str = setting(
        HexColor(),
        default="#77818d",
        description="Low temperature foreground color in #RRGGBB format."
    )

@dataclass(frozen=True, slots=True)
class WeatherTexts(Record):
    unavailable: str = setting(
        Text(single_line=True),
        default="날씨 연결 실패",
        description="Message displayed when a weather request fails."
    )

@dataclass(frozen=True, slots=True)
class WeatherSpacing(Spacing):
    after_rows: int = setting(Spacing.ROWS_RULE, default=1)


@dataclass(frozen=True, slots=True)
class WeatherConfig(ElementConfig):
    WEEKDAY_LABELS: ClassVar[tuple[str, ...]] = ("ᴍᴏɴ", "ᴛᴜᴇ", "ᴡᴇᴅ", "ᴛʜᴜ", "ꜰʀɪ", "ꜱᴀᴛ", "ꜱᴜɴ")
    spacing: WeatherSpacing = setting(Nested(WeatherSpacing), factory=WeatherSpacing)
    location: WeatherLocation | None = setting(Nullable(Nested(WeatherLocation)), default=None,
        description="Forecast location. null disables weather requests. Supply your own latitude and longitude.\nObject format: {\"latitude\": <latitude>, \"longitude\": <longitude>, \"label\": \"<location name>\"}")
    temperature_unit: TemperatureUnit = setting(
        Choice(TemperatureUnit),
        default=TemperatureUnit.CELSIUS,
        description="Temperature unit: celsius or fahrenheit."
    )
    days: int = setting(
        Number(minimum=1, maximum=7, integer=True),
        default=7,
        description="Number of forecast days to display. Fewer days are shown when space is limited."
    )
    refresh_minutes: int = setting(
        Number(minimum=5, maximum=1440, integer=True),
        default=30,
        description="Weather refresh interval in minutes. Reopening the dock reuses a valid cache until this interval expires."
    )
    style: WeatherStyle = setting(
        Nested(WeatherStyle),
        factory=WeatherStyle,
        description="Weather foreground and low-temperature colors."
    )
    texts: WeatherTexts = setting(
        Nested(WeatherTexts),
        factory=WeatherTexts,
        description="Weather status messages."
    )
    width_ratio: float = setting(
        Number(minimum=0, maximum=1, exclusive_minimum=True),
        default=0.5,
        description="Target weather width as a fraction of the dock width. Enough space is retained to prevent temperature overlap."
    )
    icon_offset_columns: int = setting(
        Number(integer=True),
        default=-1,
        description="Horizontal icon offset in cells. Negative moves left; positive moves right. Clamped within each column."
    )
    icons: WeatherIcons = setting(
        Nested(WeatherIcons),
        factory=WeatherIcons,
        description="Icons or single-line text for each weather condition."
    )
    weekday_labels: tuple[str, ...] = setting(
        Sequence(
            Text(nonblank=True, single_line=True),
            minimum=len(WEEKDAY_LABELS),
            maximum=len(WEEKDAY_LABELS)
        ),
        default=WEEKDAY_LABELS,
        description="Weather weekday labels in Monday-to-Sunday order. Small capitals, regular letters, and other text are supported.",
    )

    def weekday_label(self, day: date) -> str:
        return self.weekday_labels[day.weekday()]

    @property
    def can_fetch(self):
        return self.enabled and self.location is not None

    @property
    def needs_clock(self) -> bool:
        return self.enabled and self.location is None

    @property
    def refresh_seconds(self):
        return self.refresh_minutes * 60

    @property
    def request_key(self):
        return (self.location, self.temperature_unit, self.days)
