"""동기 공급자 호출의 실제 수명을 추적한다. 취소된 소비자도 호출 슬롯을 조기 반환하지 않는다."""

import asyncio
import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from llm.compat import dataclass


_current_calls = ContextVar("llm_provider_calls", default=None)


@dataclass(frozen=True)
class ProviderLimits:
    """백엔드 전체 호출/대기 상한. transport timeout은 completion 인자로 별도 설정한다."""

    max_active: int = 8
    max_waiting: int = 32
    wait_seconds: float = 30.0

    def __post_init__(self):
        for name, minimum in (("max_active", 1), ("max_waiting", 0)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if (isinstance(self.wait_seconds, bool) or not isinstance(self.wait_seconds, (int, float))
                or not math.isfinite(self.wait_seconds) or self.wait_seconds < 0):
            raise ValueError("wait_seconds must be finite and nonnegative")


class ProviderCapacityError(RuntimeError):
    """실행/대기 한도 또는 대기 시간이 소진되어 공급자 호출을 시작하지 못했다."""


class ProviderCalls:
    """공급자에 독립적인 슬롯 소유자. 별도 백엔드는 별도 인스턴스를 사용한다."""

    def __init__(self, limits: Optional[ProviderLimits] = None):
        self.limits = limits or ProviderLimits()
        self._mutex = threading.Lock()
        self._active = self._waiting = 0
        self._waiters = set()

    @property
    def stats(self):
        with self._mutex:
            return {"active": self._active, "waiting": self._waiting}

    @contextmanager
    def scope(self):
        """중첩 엔진/async Task에 공유하되 프로세스 전역 호출 제한은 만들지 않는다."""
        token = _current_calls.set(self)
        try:
            yield self
        finally:
            _current_calls.reset(token)

    async def acquire(self):
        deadline = time.monotonic() + self.limits.wait_seconds
        loop, ready = asyncio.get_running_loop(), asyncio.Event()
        with self._mutex:
            if self._active < self.limits.max_active:
                self._active += 1
                return
            if self._waiting >= self.limits.max_waiting:
                raise ProviderCapacityError("Provider waiting capacity exhausted")
            self._waiting += 1
            self._waiters.add((loop, ready))
        try:
            while True:
                with self._mutex:
                    if self._active < self.limits.max_active:
                        self._active += 1
                        return
                    ready.clear()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderCapacityError("Provider capacity wait timed out")
                try:
                    await asyncio.wait_for(ready.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise ProviderCapacityError("Provider capacity wait timed out") from None
        finally:
            with self._mutex:
                self._waiting -= 1
                self._waiters.discard((loop, ready))

    def release(self):
        """실제 호출과 스트림 close가 종료된 스레드에서 호출한다."""
        with self._mutex:
            if self._active <= 0:
                raise RuntimeError("Provider slot is not held")
            self._active -= 1
            for loop, ready in self._waiters:
                try:
                    loop.call_soon_threadsafe(ready.set)
                except RuntimeError:
                    pass  # 종료된 루프는 새 호출을 대기하지 않는다.


def current_calls():
    return _current_calls.get()
