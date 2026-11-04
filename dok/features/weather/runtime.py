"""Forecast cache, refresh schedule and failure state owned by weather."""

from dataclasses import replace
import asyncio

from ...integrations.weather.open_meteo import OpenMeteo
from .models import WeatherSnapshot


class WeatherRuntime:
    def __init__(self, invalidate, backend=None):
        self._invalidate = invalidate
        self._backend = backend if backend is not None else OpenMeteo()
        self._snapshot = WeatherSnapshot()
        self._request_key = None
        self._next_fetch = 0.0
        self._config = None

    def _publish(self, snapshot):
        self._snapshot = snapshot
        self._invalidate()

    @property
    def snapshot(self):
        return self._snapshot

    def snapshot_for(self, config):
        return self._snapshot if config.request_key == self._request_key else WeatherSnapshot()

    def prepare(self, config):
        """Called only after the previous refresh task has finished."""
        if config.request_key != self._request_key:
            self._request_key = config.request_key
            self._next_fetch = 0.0
            self._publish(WeatherSnapshot())

    async def run(self, config, now):
        if self._config is not None and self._config.refresh_minutes != config.refresh_minutes:
            self._next_fetch = 0.0
        self._config = config
        if not config.can_fetch:
            return
        loop = asyncio.get_running_loop()
        while True:
            delay = self._next_fetch - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                async with asyncio.timeout(OpenMeteo.TIMEOUT):
                    days = await self._backend.fetch(config)
                self._publish(WeatherSnapshot(days, now()))
            except Exception as exc:
                self._publish(replace(self._snapshot, error=str(exc) or type(exc).__name__))
            self._next_fetch = loop.time() + config.refresh_seconds
