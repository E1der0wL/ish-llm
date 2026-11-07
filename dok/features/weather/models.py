"""Weather conditions, temperature units and forecast snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from ...locales import Locale, Message


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

    def label(self, locale=Locale()):
        return locale.text({self.CLEAR: Message.CLEAR, self.PARTLY_CLOUDY: Message.PARTLY_CLOUDY,
            self.CLOUDY: Message.CLOUDY, self.FOG: Message.FOG, self.RAIN: Message.RAIN,
            self.SNOW: Message.SNOW, self.THUNDER: Message.THUNDERSTORM, self.UNKNOWN: Message.UNKNOWN}[self])


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
class WeatherHour:
    time: datetime
    temperature: float | None
    apparent: float | None
    kind: WeatherKind
    precipitation_probability: float | None
    wind_speed: float | None = None
    humidity: float | None = None
    rain: float | None = None
    uv_index: float | None = None

    @staticmethod
    def details(hour: WeatherHour | None, unit: TemperatureUnit, locale=Locale()):
        """Display labels and canonical units for the selected hour, or placeholders."""
        items = (("󰔏", Message.FEELS_LIKE, hour.apparent if hour else None, unit.symbol),
                 ("󰖝", Message.WIND, hour.wind_speed if hour else None, "km/h"),
                 ("󰖎", Message.HUMIDITY, hour.humidity if hour else None, "%"),
                 ("󰖗", Message.RAINFALL, hour.rain if hour else None, "mm"),
                 ("󰖙", Message.UV_INDEX, hour.uv_index if hour else None, ""))
        return tuple((icon, locale.text(label), ("--" if value is None else f"{value:g}") + (" " + suffix if suffix else ""))
                     for icon, label, value, suffix in items)


@dataclass(frozen=True, slots=True)
class CurrentWeather:
    time: datetime
    temperature: float | None
    apparent: float | None
    kind: WeatherKind


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    days: tuple[ForecastDay, ...] = ()
    updated_at: datetime | None = None
    error: str = ""
    current: CurrentWeather | None = None
    hours: tuple[WeatherHour, ...] = ()
    timezone: str = ""

    @classmethod
    def unknown(cls, start: date, count: int) -> WeatherSnapshot:
        """Create dated placeholders without inventing conditions or temperatures."""
        return cls(tuple(
            ForecastDay(start + timedelta(days=index), WeatherKind.UNKNOWN, None, None)
            for index in range(count)
        ))
