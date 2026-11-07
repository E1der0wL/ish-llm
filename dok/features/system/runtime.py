"""One bounded sampler whose blocking read finishes before replacement."""
import asyncio
import sys
from collections import deque
from collections.abc import Callable
import time

from ...core.tasks import TaskSlot, TaskStarter, run_blocking
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
        self._history = deque()

    async def _read(self, config: SystemConfig) -> SystemReading:
        return await run_blocking(self._backend.read, config.interfaces)

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
            await asyncio.sleep(config.refresh_s)

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
            self._history = deque(maxlen=min(config.history, sys.maxsize))
        self._active, self._config = active, config
        self._task.sync(active=active, key=config, run=lambda: self._run(config), start=start_task)

    def close(self) -> None:
        self._active = False
        self._task.close()
        self._snapshot = SystemSnapshot()
        self._history.clear()
