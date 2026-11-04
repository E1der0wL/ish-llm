"""Weather conditions, temperature units and forecast snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum


class TemperatureUnit(StrEnum):
    CELSIUS = "celsius"
    FAHRENHEIT = "fahrenheit"

    @property
    def symbol(self):
        return "°C" if self == self.CELSIUS else "°F"


class WeatherKind(StrEnum):
    CLEAR = "clear"
    PARTLY_CLOUDY = "partly_cloudy"
    CLOUDY = "cloudy"
    FOG = "fog"
    RAIN = "rain"
    SNOW = "snow"
    THUNDER = "thunder"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ForecastDay:
    day: date
    kind: WeatherKind
    high: float | None
    low: float | None

    @property
    def temperatures(self):
        return " ".join(self.temperature_parts)

    @property
    def temperature_parts(self):
        return tuple("--°" if value is None else f"{value:.0f}°" for value in (self.high, self.low))


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    days: tuple[ForecastDay, ...] = ()
    updated_at: datetime | None = None
    error: str = ""

    @classmethod
    def unknown(cls, start: date, count: int) -> WeatherSnapshot:
        """Create dated placeholders without inventing conditions or temperatures."""
        return cls(tuple(
            ForecastDay(start + timedelta(days=index), WeatherKind.UNKNOWN, None, None)
            for index in range(count)
        ))
