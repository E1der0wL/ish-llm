"""Open-Meteo requests, response limits and forecast decoding."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from ...core.records import Nullable, Number
from ...features.weather.models import ForecastDay, WeatherKind, WeatherSnapshot, CurrentWeather, WeatherHour


class OpenMeteo:
    URL = "https://api.open-meteo.com/v1/forecast"
    ATTRIBUTION = "Open-Meteo"
    TIMEOUT = 10.0
    MAX_BYTES = 256 * 1024
    DAILY = "daily"
    DATES = "time"
    CODE = "weather_code"
    HIGH = "temperature_2m_max"
    LOW = "temperature_2m_min"
    CURRENT = "current"
    HOURLY = "hourly"
    TEMPERATURE = "temperature_2m"
    APPARENT = "apparent_temperature"
    PROBABILITY = "precipitation_probability"
    WIND = "wind_speed_10m"
    HUMIDITY = "relative_humidity_2m"
    RAIN = "rain"
    UV = "uv_index"
    TIMEZONE = "timezone"

    @classmethod
    def hourly_fields(cls):
        return (cls.TEMPERATURE, cls.APPARENT, cls.CODE, cls.PROBABILITY, cls.WIND, cls.HUMIDITY, cls.RAIN, cls.UV)

    @classmethod
    def params(cls, config):
        if not config.can_fetch:
            raise ValueError("Weather location required")
        return {"latitude": config.location.lat, "longitude": config.location.lon,
                "daily": ",".join((cls.CODE, cls.HIGH, cls.LOW)), "timezone": "auto",
                "current": ",".join((cls.TEMPERATURE, cls.APPARENT, cls.CODE)),
                "hourly": ",".join(cls.hourly_fields()), "wind_speed_unit": "kmh", "precipitation_unit": "mm",
                "timeformat": "unixtime",
                "forecast_days": config.days, "temperature_unit": config.unit.value}

    @staticmethod
    def kind(code):
        if code in (0, 1):
            return WeatherKind.CLEAR
        if code == 2:
            return WeatherKind.PARTLY_CLOUDY
        if code == 3:
            return WeatherKind.CLOUDY
        if code in (45, 48):
            return WeatherKind.FOG
        if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            return WeatherKind.RAIN
        if code in (71, 73, 75, 77, 85, 86):
            return WeatherKind.SNOW
        if code in (95, 96, 97, 99):
            return WeatherKind.THUNDER
        return WeatherKind.UNKNOWN

    @classmethod
    def decode(cls, data, count):
        if not isinstance(data, dict) or not isinstance(data.get(cls.DAILY), dict):
            raise ValueError("Missing daily forecast")
        daily = data[cls.DAILY]
        arrays = [daily.get(field) for field in (cls.DATES, cls.CODE, cls.HIGH, cls.LOW)]
        if any(not isinstance(items, list) or len(items) != count for items in arrays):
            raise ValueError("Invalid forecast arrays")
        result = []
        for raw_date, code, high, low in zip(*arrays):
            if code is not None:
                Number(minimum=0, integer=True).decode(code)
            day = (datetime.fromtimestamp(raw_date, ZoneInfo(data[cls.TIMEZONE])).date() if isinstance(raw_date, (int, float))
                   else date.fromisoformat(raw_date))
            result.append(ForecastDay(day, cls.kind(code),
                                      Nullable(Number()).decode(high), Nullable(Number()).decode(low)))
        if any(left.day >= right.day for left, right in zip(result, result[1:])):
            raise ValueError("Forecast dates must increase")
        return tuple(result)

    @classmethod
    def decode_snapshot(cls, data, count):
        days = cls.decode(data, count)
        zone_name = data.get(cls.TIMEZONE)
        zone = ZoneInfo(zone_name)
        number = Nullable(Number())
        def timestamp(value):
            Number().decode(value)
            return datetime.fromtimestamp(value, timezone.utc).astimezone(zone)
        def kind(value):
            Nullable(Number(minimum=0, integer=True)).decode(value)
            return cls.kind(value)
        current = data.get(cls.CURRENT)
        if not isinstance(current, dict):
            raise ValueError("Missing current weather")
        observed = CurrentWeather(timestamp(current[cls.DATES]), number.decode(current[cls.TEMPERATURE]),
                                  number.decode(current[cls.APPARENT]), kind(current[cls.CODE]))
        hourly = data.get(cls.HOURLY)
        if not isinstance(hourly, dict):
            raise ValueError("Missing hourly forecast")
        arrays = [hourly.get(field) for field in (cls.DATES, *cls.hourly_fields())]
        if not arrays or not isinstance(arrays[0], list) or not arrays[0] or any(not isinstance(a, list) or len(a) != len(arrays[0]) for a in arrays):
            raise ValueError("Invalid hourly arrays")
        percent = Nullable(Number(minimum=0, maximum=100))
        nonnegative = Nullable(Number(minimum=0))
        hours = tuple(WeatherHour(timestamp(t), number.decode(v), number.decode(a), kind(c),
                                 percent.decode(p), nonnegative.decode(w), percent.decode(h),
                                 nonnegative.decode(r), nonnegative.decode(u)) for t, v, a, c, p, w, h, r, u in zip(*arrays))
        if any(a.time.timestamp() >= b.time.timestamp() for a, b in zip(hours, hours[1:])):
            raise ValueError("Hourly times must increase")
        return WeatherSnapshot(days=days, current=observed, hours=hours, timezone=zone_name)

    async def fetch(self, config):
        import json

        async with httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=False) as client:
            async with client.stream("GET", self.URL, params=self.params(config)) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.MAX_BYTES:
                        raise ValueError("Weather response too large")
        return self.decode_snapshot(json.loads(content), config.days)
