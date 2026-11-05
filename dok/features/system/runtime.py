"""One bounded sampler whose blocking read finishes before replacement."""
import asyncio
from collections import deque
from collections.abc import Callable
import time

from ...core.tasks import TaskSlot, TaskStarter
from ...integrations.system.psutil import PsutilBackend
from .config import SystemConfig
from .models import SystemBackend, SystemReading, SystemSnapshot


class SystemRuntime:
    def __init__(self, changed: Callable[[], None], backend: SystemBackend | None = None):
        self._changed = changed
        self._backend = backend or PsutilBackend()
        self._task = TaskSlot(changed)
        self._config = SystemConfig()
        self._active = False
        self._snapshot = SystemSnapshot()
        self._history = deque(maxlen=self._config.history_samples)

    async def _read(self, config: SystemConfig) -> SystemReading:
        job = asyncio.create_task(asyncio.to_thread(self._backend.read, config.interfaces))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            # A cancelled to_thread await does not stop the worker. Drain it so
            # TaskSlot cannot start a second read while this one is still alive.
            while not job.done():
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not job.cancelled():
                job.exception()
            raise

    async def _run(self, config: SystemConfig) -> None:
        previous = None
        while True:
            try:
                current = await self._read(config)
            except Exception as exc:
                current = SystemReading(time.monotonic(), diagnostic=str(exc))
            if not self._active or config != self._config:
                return
            self._snapshot = SystemSnapshot.between(previous, current)
            self._history.append(self._snapshot)
            previous = current
            self._changed()
            await asyncio.sleep(config.refresh_seconds)

    @property
    def snapshot(self) -> SystemSnapshot:
        return self._snapshot

    @property
    def history(self) -> tuple[SystemSnapshot, ...]:
        return tuple(self._history)

    def sync(self, *, active: bool, config: SystemConfig, start_task: TaskStarter) -> None:
        active = active and config.enabled and not self._task.closed
        if active != self._active or config != self._config:
            self._snapshot = SystemSnapshot()
            self._history = deque(maxlen=config.history_samples)
        self._active, self._config = active, config
        self._task.sync(active=active, key=config, run=lambda: self._run(config), start=start_task)

    def close(self) -> None:
        self._active = False
        self._task.close()
        self._snapshot = SystemSnapshot()
        self._history.clear()
