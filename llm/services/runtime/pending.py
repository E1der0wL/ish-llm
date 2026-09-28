"""취소 후 남은 비동기 작업과 Task 소유권 해제 사이의 경쟁을 제어한다."""

import asyncio
import threading


class PendingWork:
    def __init__(self):
        self._tasks = set()
        self._changed = None
        self._on_idle = None
        self._lock = threading.Lock()

    def _done(self, task):
        if not task.cancelled():
            task.exception()
        with self._lock:
            self._tasks.discard(task)
            callback = self._on_idle if not self._tasks else None
            if callback is not None:
                self._on_idle = None
        self.wake()
        if callback is not None:
            callback()

    @property
    def active(self):
        with self._lock:
            return len(self._tasks)

    def track(self, task):
        with self._lock:
            self._tasks.add(task)
        task.add_done_callback(self._done)

    def when_idle(self, callback):
        """스토리지 스레드의 종료 요청과 이벤트 루프의 완료 알림을 원자적으로 연결한다."""
        with self._lock:
            waiting = bool(self._tasks)
            if waiting:
                self._on_idle = callback
        if not waiting:
            callback()

    def wake(self):
        if self._changed is not None:
            self._changed.set()

    async def wait(self):
        self._changed = asyncio.Event()
        if self.active:
            await self._changed.wait()
