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
        self._wake = asyncio.Event()
        self._fetching = False
        self._retry_pending = False

    def _publish(self, snapshot):
        self._snapshot = snapshot
        self._invalidate()

    @property
    def snapshot(self):
        return self._snapshot

    def snapshot_for(self, config):
        return self._snapshot if config.request_key == self._request_key else WeatherSnapshot()

    @property
    def retry_pending(self) -> bool:
        """Keep an explicit reopen retry active until its request finishes."""
        return self._retry_pending

    def begin_activation(self) -> None:
        """An explicit dock reopen retries failure, never fresh success or in-flight I/O."""
        if self._snapshot.error and not self._fetching:
            self._retry_pending = True
            self._next_fetch = 0.0
            self._wake.set()

    def prepare(self, config):
        """Called only after the previous refresh task has finished."""
        if config.request_key != self._request_key:
            self._request_key = config.request_key
            self._next_fetch = 0.0
            self._retry_pending = False
            self._wake.clear()
            self._publish(WeatherSnapshot())

    async def run(self, config, now):
        if self._config is not None and self._config.refresh_min != config.refresh_min:
            self._next_fetch = 0.0
        self._config = config
        if not config.can_fetch:
            return
        loop = asyncio.get_running_loop()
        while True:
            delay = self._next_fetch - loop.time()
            if delay > 0:
                try:
                    async with asyncio.timeout(delay):
                        await self._wake.wait()
                except TimeoutError:
                    pass
            self._wake.clear()
            self._fetching = True
            try:
                async with asyncio.timeout(OpenMeteo.TIMEOUT):
                    snapshot = await self._backend.fetch(config)
                snapshot = replace(snapshot, updated_at=now(), error="")
            except Exception as exc:
                snapshot = replace(self._snapshot, error=str(exc) or type(exc).__name__)
            finally:
                self._fetching = False
            # Cancellation preserves pending retry intent for the replacement task.
            self._retry_pending = False
            self._next_fetch = loop.time() + config.refresh_s
            self._publish(snapshot)
