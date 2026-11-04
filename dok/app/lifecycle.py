"""Coordinate the active lifetime of clock and weather background tasks."""

import asyncio

from ..features.clock.runtime import ClockRuntime
from ..features.weather.runtime import WeatherRuntime


class DashboardRuntime:
    """Keep one task group active; feature runtimes retain their own cached data."""

    def __init__(self, invalidate, backend=None):
        self._invalidate = invalidate
        self._clock = ClockRuntime(invalidate)
        self._weather = WeatherRuntime(invalidate, backend)
        self._task = None
        self._config = None
        self._closed = False

    async def _run(self, config):
        async with asyncio.TaskGroup() as group:
            if config.widgets.needs_clock:
                group.create_task(self._clock.run(config.clock, config.widgets.date, config.widgets.time))
            group.create_task(self._weather.run(config.widgets.weather, config.clock.now))

    @property
    def snapshot(self):
        return self._weather.snapshot

    def snapshot_for(self, config):
        return self._weather.snapshot_for(config.widgets.weather)

    def sync(self, *, active, config, app):
        active = active and not self._closed and config.widgets.needs_runtime
        if self._task is not None and not self._task.done():
            if active and self._config == config and not self._task.cancelling():
                return
            if not self._task.cancelling():
                self._task.cancel()
            return
        self._weather.prepare(config.widgets.weather)
        if active:
            self._config = config
            self._task = app.create_background_task(self._run(config))
            self._task.add_done_callback(lambda task: self._invalidate())

    def close(self):
        self._closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
