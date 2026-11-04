"""Open-Meteo requests, response limits and forecast decoding."""
from __future__ import annotations

from datetime import date

import httpx

from ...core.records import Nullable, Number
from ...features.weather.models import ForecastDay, WeatherKind


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

    @classmethod
    def params(cls, config):
        if not config.can_fetch:
            raise ValueError("Weather location required")
        return {"latitude": config.location.latitude, "longitude": config.location.longitude,
                "daily": ",".join((cls.CODE, cls.HIGH, cls.LOW)), "timezone": "auto",
                "forecast_days": config.days, "temperature_unit": config.temperature_unit.value}

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
            result.append(ForecastDay(date.fromisoformat(raw_date), cls.kind(code),
                                      Nullable(Number()).decode(high), Nullable(Number()).decode(low)))
        if any(left.day >= right.day for left, right in zip(result, result[1:])):
            raise ValueError("Forecast dates must increase")
        return tuple(result)

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
        return self.decode(json.loads(content), config.days)
