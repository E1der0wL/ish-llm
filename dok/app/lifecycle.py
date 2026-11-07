"""Connect independent feature tasks to the active host application."""

from ..core.tasks import TaskSlot
from ..features.clock.config import ClockConfig, ClockSchedule
from ..features.clock.runtime import ClockRuntime
from ..features.weather.runtime import WeatherRuntime


class DashboardRuntime:
    """Reconcile each feature independently; feature owners retain their data."""

    def __init__(self, invalidate, backend=None):
        self._clock = ClockRuntime(invalidate)
        self._weather = WeatherRuntime(invalidate, backend)
        self._clock_task = TaskSlot(invalidate)
        self._weather_task = TaskSlot(invalidate)
        self._clock_config = ClockConfig()

    def _now(self):
        # An in-flight forecast uses the latest display timezone on publication.
        return self._clock_config.now()

    @property
    def snapshot(self):
        return self._weather.snapshot

    def snapshot_for(self, config):
        return self._weather.snapshot_for(config.widgets.weather)

    def begin_activation(self):
        self._weather.begin_activation()

    def sync(self, *, active, config, app, weather_active=False, clock_active=False, dock_active=False):
        if self._clock_task.closed:
            return
        self._clock_config = config.clock
        schedule = (config.clock.schedule(config.widgets.date, config.widgets.time) if active
                    else ClockSchedule.for_formats(config.clock))
        self._clock_task.sync(
            active=(active and config.widgets.needs_clock) or clock_active, key=schedule,
            run=lambda: self._clock.run(schedule), start=app.create_background_task,
        )
        weather = config.widgets.weather
        if not self._weather_task.running:
            self._weather.prepare(weather)
        self._weather_task.sync(
            active=(active or weather_active or (dock_active and self._weather.retry_pending)) and weather.can_fetch,
            key=weather.polling_key,
            run=lambda: self._weather.run(weather, self._now), start=app.create_background_task,
        )

    def close(self):
        self._clock_task.close()
        self._weather_task.close()
