"""공유 공급자 호출 제한. 동기 스트림과 비동기 모델 호출의 실제 수명을 추적한다."""

from llm.providers.codes import ProviderErrorCode
import asyncio
import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, ClassVar

from dataclasses import dataclass, field, fields
from llm.errors import CodedError


_CALL_CONTEXT = "llm_provider_calls"
_UNBOUND_CALLS = None
_current_calls = ContextVar(_CALL_CONTEXT, default=_UNBOUND_CALLS)


@dataclass(frozen=True)
class ProviderLimits:
    """백엔드 전체 호출/대기 상한. transport timeout은 completion 인자로 별도 설정한다."""

    MINIMUM: ClassVar[str] = "minimum"
    UNSPECIFIED: ClassVar[None] = None
    MINIMUM_SECONDS: ClassVar[int] = 0
    INVALID_COUNT: ClassVar[str] = "{name} must be an integer >= {minimum}"
    INVALID_WAIT: ClassVar[str] = "wait_seconds must be finite and nonnegative"

    max_active: Optional[int] = field(default=UNSPECIFIED, metadata={MINIMUM: 1})
    max_waiting: Optional[int] = field(default=UNSPECIFIED, metadata={MINIMUM: 0})
    wait_seconds: Optional[float] = None

    def __post_init__(self):
        for item in fields(ProviderLimits):
            if self.MINIMUM not in item.metadata:
                continue
            minimum = item.metadata[self.MINIMUM]
            value = getattr(self, item.name)
            if value is not self.UNSPECIFIED and (type(value) is not int or value < minimum):
                raise ValueError(self.INVALID_COUNT.format(name=item.name, minimum=minimum))
        if self.wait_seconds is not self.UNSPECIFIED and (
            isinstance(self.wait_seconds, bool)
            or not isinstance(self.wait_seconds, (int, float))
            or not math.isfinite(self.wait_seconds)
            or self.wait_seconds < self.MINIMUM_SECONDS
        ):
            raise ValueError(self.INVALID_WAIT)


@dataclass(frozen=True, slots=True)
class ProviderCallStats:
    active: int
    waiting: int

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


class ProviderCapacityError(CodedError, RuntimeError):
    """실행/대기 한도 또는 대기 시간이 소진되어 공급자 호출을 시작하지 못했다."""

    code = ProviderErrorCode.CAPACITY


class ProviderCalls:
    """공급자에 독립적인 슬롯 소유자. 별도 백엔드는 별도 인스턴스를 사용한다."""

    UNSPECIFIED = ProviderLimits.UNSPECIFIED
    EMPTY_COUNT = 0
    SLOT_INCREMENT = 1
    INVALID_LIMITS = "Provider limits must be ProviderLimits or None"
    WAITING_FULL = "Provider waiting capacity exhausted"
    WAIT_EXPIRED = "Provider capacity wait timed out"
    SLOT_NOT_HELD = "Provider slot is not held"

    def __init__(self, limits: Optional[ProviderLimits] = UNSPECIFIED):
        if limits is not self.UNSPECIFIED and not isinstance(limits, ProviderLimits):
            raise TypeError(self.INVALID_LIMITS)
        self.limits = limits if limits is not self.UNSPECIFIED else ProviderLimits()
        self._mutex = threading.Lock()
        self._active = self._waiting = self.EMPTY_COUNT
        self._waiters = set()

    @property
    def stats(self):
        with self._mutex:
            return ProviderCallStats(self._active, self._waiting).to_dict()

    @contextmanager
    def scope(self):
        """중첩 엔진/async Task에 공유하되 프로세스 전역 호출 제한은 만들지 않는다."""
        token = _current_calls.set(self)
        try:
            yield self
        finally:
            _current_calls.reset(token)

    async def acquire(self):
        deadline = (
            self.UNSPECIFIED
            if self.limits.wait_seconds is self.UNSPECIFIED
            else time.monotonic() + self.limits.wait_seconds
        )
        loop, ready = asyncio.get_running_loop(), asyncio.Event()
        with self._mutex:
            if self.limits.max_active is self.UNSPECIFIED or self._active < self.limits.max_active:
                self._active += self.SLOT_INCREMENT
                return
            if self.limits.max_waiting is not self.UNSPECIFIED and self._waiting >= self.limits.max_waiting:
                raise ProviderCapacityError(self.WAITING_FULL)
            self._waiting += self.SLOT_INCREMENT
            self._waiters.add((loop, ready))
        try:
            while True:
                with self._mutex:
                    if self.limits.max_active is self.UNSPECIFIED or self._active < self.limits.max_active:
                        self._active += self.SLOT_INCREMENT
                        return
                    ready.clear()
                remaining = self.UNSPECIFIED if deadline is self.UNSPECIFIED else deadline - time.monotonic()
                if remaining is not self.UNSPECIFIED and remaining <= ProviderLimits.MINIMUM_SECONDS:
                    raise ProviderCapacityError(self.WAIT_EXPIRED)
                try:
                    await asyncio.wait_for(ready.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise ProviderCapacityError(self.WAIT_EXPIRED) from None
        finally:
            with self._mutex:
                self._waiting -= self.SLOT_INCREMENT
                self._waiters.discard((loop, ready))

    def release(self):
        """실제 호출과 스트림 close가 종료된 스레드에서 호출한다."""
        with self._mutex:
            if self._active <= self.EMPTY_COUNT:
                raise RuntimeError(self.SLOT_NOT_HELD)
            self._active -= self.SLOT_INCREMENT
            for loop, ready in self._waiters:
                try:
                    loop.call_soon_threadsafe(ready.set)
                except RuntimeError:
                    pass  # 종료된 루프는 새 호출을 대기하지 않는다.


def current_calls():
    return _current_calls.get()
