"""One replaceable task, with cancellation completed before replacement."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

type TaskFactory = Callable[[], Coroutine[Any, Any, None]]
type TaskStarter = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]


class TaskSlot:
    """Own task lifetime only; activation and retry policy belong to the caller.

    Completion requests reconciliation, but never starts a task itself. The host
    must still be active on the next sync, including after prompt shutdown.
    """

    def __init__(self, changed: Callable[[], None]):
        self._changed = changed
        self._task: asyncio.Task[None] | None = None
        self._key: object = None
        self._closed = False

    def _finished(self, task: asyncio.Task[None]) -> None:
        if task is self._task and not self._closed:
            self._changed()

    def _cancel(self) -> None:
        if self.running and not self._task.cancelling():
            self._task.cancel()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def closed(self) -> bool:
        return self._closed

    def sync(self, *, active: bool, key: object, run: TaskFactory, start: TaskStarter) -> bool:
        """Return true only when the requested task is running or was started."""
        active = active and not self._closed
        if self.running:
            if active and self._key == key and not self._task.cancelling():
                return True
            self._cancel()
            return False
        if not active:
            return False
        coroutine = run()
        try:
            task = start(coroutine)
        except BaseException:
            coroutine.close()
            raise
        self._key, self._task = key, task
        task.add_done_callback(self._finished)
        return True

    def close(self) -> None:
        self._closed = True
        self._cancel()
