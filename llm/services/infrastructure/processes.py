"""Linux 프로세스 그룹의 수명 종료를 공통으로 처리한다."""

import asyncio
import os
import signal
import threading
from contextlib import contextmanager
from contextvars import ContextVar


_cancellation = ContextVar("llm_process_cancellation", default=None)


class ProcessCancellation:
    """스토리지 스레드의 child만 취소한다. 파일 트랜잭션 자체는 계속 drain한다."""
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._callbacks = set()

    @contextmanager
    def scope(self):
        token = _cancellation.set(self)
        try:
            yield
        finally:
            _cancellation.reset(token)

    @contextmanager
    def register(self, callback):
        with self._lock:
            self._callbacks.add(callback)
            cancelled = self._cancelled
        if cancelled:
            callback()
        try:
            yield
        finally:
            with self._lock:
                self._callbacks.discard(callback)

    def cancel(self):
        with self._lock:
            self._cancelled = True
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            callback()


def current_process_cancellation():
    return _cancellation.get()


async def kill_process_tree(process, *, timeout_seconds=5):
    """새 세션으로 시작한 그룹을 종료한다. setsid로 이탈한 자식은 sandbox가 필요하다."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout_seconds)
    except asyncio.TimeoutError:
        raise RuntimeError("Process group did not terminate within cleanup deadline") from None
