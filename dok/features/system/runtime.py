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
        self._idle = False
        self._wake = asyncio.Event()

    async def _read(self, config: SystemConfig) -> SystemReading:
        return await run_blocking(self._backend.read, config.interfaces)

    async def _run(self, config: SystemConfig) -> None:
        previous = None
        while self._active and config == self._config and not self._task.closed:
            try:
                current = await self._read(config)
            except Exception as exc:
                current = SystemReading(time.monotonic(), diagnostic=str(exc))
            if not self._active or config != self._config:
                return
            before = (self._snapshot.display_key, tuple(item.display_key for item in self._history))
            self._snapshot = SystemSnapshot.between(previous, current)
            self._history.append(self._snapshot)
            previous = current
            after = (self._snapshot.display_key, tuple(item.display_key for item in self._history))
            if after != before:
                self._changed()
            self._wake.clear()
            try:
                async with asyncio.timeout(config.interval(self._idle)):
                    await self._wake.wait()
            except TimeoutError:
                pass

    @property
    def snapshot(self) -> SystemSnapshot:
        return self._snapshot

    @property
    def history(self) -> tuple[SystemSnapshot, ...]:
        return tuple(self._history)

    def sync(self, *, active: bool, config: SystemConfig, start_task: TaskStarter, idle=False) -> None:
        active = active and config.enabled and not self._task.closed
        if idle != self._idle:
            self._idle = idle
            self._wake.set()
        if active != self._active or config != self._config:
            self._wake.set()
            self._snapshot = SystemSnapshot()
            self._history = deque(maxlen=min(config.history, sys.maxsize))
        self._active, self._config = active, config
        self._task.sync(active=active, key=config, run=lambda: self._run(config), start=start_task)

    def close(self) -> None:
        self._active = False
        self._task.close()
        self._wake.set()
        self._snapshot = SystemSnapshot()
        self._history.clear()
