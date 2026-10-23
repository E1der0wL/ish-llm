"""논리 작업의 재시도 계산과 Application 선택 계약. 오류/효과/저장은 호출자가 소유한다."""

import asyncio
import inspect
import math
import random
import time
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, ClassVar


class RetryBackoff(StrEnum):
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """재시도 선택. changes=None/{}는 같은 인자, dict는 선언된 최상위 필드 교체다.

    선택기가 None을 반환하면 중단한다. null 값은 삭제가 아니라 명시적 값이다.
    """

    UNCHANGED: ClassVar[None] = None
    INVALID_CHANGES: ClassVar[str] = "Retry changes must be a string-keyed object"

    changes: dict | None = UNCHANGED

    def __post_init__(self):
        if self.changes is not self.UNCHANGED and (not isinstance(self.changes, dict)
                or any(not isinstance(k, str) for k in self.changes)):
            raise TypeError(self.INVALID_CHANGES)
        object.__setattr__(self, RetryDecision.changes.__name__, deepcopy(self.changes))


@dataclass(frozen=True, slots=True)
class RetryFailure:
    """Application에 전달하는 한 시도의 사본. code만으로 재실행 권한을 만들지 않는다."""

    operation: str
    arguments: dict
    code: str
    message: str
    attempt: int
    retryable: bool
    changeable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """기존 설정을 계산에만 사용한다. missing은 최초 호출만, 기한은 시도마다 갱신하지 않는다.

    backoff는 호출 구현의 기존 fixed/exponential 알고리즘 선택이며 새 사용자 기본값이 아니다.
    실제 deadline 초과 오류 코드와 영속 횟수는 각 실행 경계가 소유한다.
    """

    INITIAL_ATTEMPTS: ClassVar[int] = 1
    UNSPECIFIED: ClassVar[None] = None
    NO_DELAY: ClassVar[int] = 0
    EXPIRED: ClassVar[int] = 0
    NO_JITTER: ClassVar[bool] = False
    INVALID_ATTEMPTS: ClassVar[str] = "Retry max_attempts must be a positive integer"
    INVALID_DELAY: ClassVar[str] = "Retry delay must be finite and nonnegative"
    INVALID_DEADLINE: ClassVar[str] = "Retry deadline must be finite"
    UNKNOWN_BACKOFF: ClassVar[str] = "Unknown retry backoff"
    DEADLINE_EXCEEDED: ClassVar[str] = "Retry deadline exceeded"
    INVALID_MINIMUM: ClassVar[str] = "Retry minimum delay must be finite and nonnegative"
    UNBOUNDED_DELAY: ClassVar[str] = "Retry delay is not finite; configure max_delay_seconds"

    max_attempts: int | None = UNSPECIFIED
    delay_seconds: float | None = UNSPECIFIED
    max_delay_seconds: float | None = UNSPECIFIED
    deadline: float | None = UNSPECIFIED
    backoff: str = RetryBackoff.FIXED.value

    def __post_init__(self):
        if self.max_attempts is not self.UNSPECIFIED and (type(self.max_attempts) is not int
                                             or self.max_attempts < self.INITIAL_ATTEMPTS):
            raise ValueError(self.INVALID_ATTEMPTS)
        for value in (self.delay_seconds, self.max_delay_seconds):
            if value is not self.UNSPECIFIED and (type(value) not in (int, float) or not math.isfinite(value) or value < self.NO_DELAY):
                raise ValueError(self.INVALID_DELAY)
        if self.deadline is not self.UNSPECIFIED and (type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)):
            raise ValueError(self.INVALID_DEADLINE)
        if self.backoff not in tuple(RetryBackoff):
            raise ValueError(self.UNKNOWN_BACKOFF)

    @property
    def attempts(self) -> int:
        """An absent retry limit permits the initial call, never an extra retry.

        This is a calculation; the explicit configuration remains absent.
        """
        return self.INITIAL_ATTEMPTS if self.max_attempts is self.UNSPECIFIED else self.max_attempts

    @staticmethod
    def end(timeout=UNSPECIFIED, deadline=UNSPECIFIED):
        """이미 존재하는 상위 절대 기한을 넓히지 않는다."""
        end = RetryPolicy.UNSPECIFIED if timeout is RetryPolicy.UNSPECIFIED else time.monotonic() + timeout
        return deadline if end is RetryPolicy.UNSPECIFIED else end if deadline is RetryPolicy.UNSPECIFIED else min(end, deadline)

    def allows(self, attempt: int) -> bool:
        """attempt는 완료/실패한 호출의 1-based 번호다."""
        return self.max_attempts is not self.UNSPECIFIED and attempt < self.max_attempts

    def remaining(self):
        if self.deadline is self.UNSPECIFIED:
            return self.UNSPECIFIED
        value = self.deadline - time.monotonic()
        if value <= self.EXPIRED:
            raise TimeoutError(self.DEADLINE_EXCEEDED)
        return value

    def delay(self, attempt: int, *, minimum: float | None = UNSPECIFIED, jitter: bool = NO_JITTER) -> float:
        """대기 가능한 간격을 계산한다. 기한 밖의 재시도를 관찰자에게 예약했다고 알리지 않는다."""
        value = self.delay_seconds if self.delay_seconds is not self.UNSPECIFIED else self.NO_DELAY
        if value and self.backoff == RetryBackoff.EXPONENTIAL:
            # 큰 지수도 overflow로 작업 경계를 깨뜨리지 않게 cap/기한에서 처리한다.
            try:
                value = math.ldexp(value, attempt - self.INITIAL_ATTEMPTS)
            except OverflowError:
                value = math.inf
        if self.max_delay_seconds is not self.UNSPECIFIED:
            value = min(value, self.max_delay_seconds)
        if jitter and math.isfinite(value):
            value = random.uniform(self.NO_DELAY, value)
        if minimum is not self.UNSPECIFIED:
            if not math.isfinite(minimum) or minimum < self.NO_DELAY:
                raise ValueError(self.INVALID_MINIMUM)
            value = max(value, minimum)
        remaining = self.remaining()
        if remaining is not self.UNSPECIFIED and value >= remaining:
            raise TimeoutError(self.DEADLINE_EXCEEDED)
        if not math.isfinite(value):
            raise ValueError(self.UNBOUNDED_DELAY)
        return value

    async def wait(self, attempt: int):
        await asyncio.sleep(self.delay(attempt))


class RetrySelection:
    ABSENT = None
    INVALID_DECISION = "Retry selector must return RetryDecision or None"
    REQUIRED_CORRECTION = "This failure requires corrected arguments, not an unchanged retry"
    UNDECLARED_CHANGES = "Retry cannot change arguments: {fields}"


async def select_retry(failure: RetryFailure, decide: Callable | None = RetrySelection.ABSENT):
    """Application이 선택한다. 선택기가 없으면 기존 명시적 동일 인자 retry만 유지한다."""
    if decide is RetrySelection.ABSENT:
        return RetryDecision() if failure.retryable else RetrySelection.ABSENT
    result = decide(failure)
    if inspect.isawaitable(result):
        result = await result
    if result is not RetrySelection.ABSENT and not isinstance(result, RetryDecision):
        raise TypeError(RetrySelection.INVALID_DECISION)
    if result is not RetrySelection.ABSENT and not result.changes and not failure.retryable:
        raise ValueError(RetrySelection.REQUIRED_CORRECTION)
    return result


def change_arguments(arguments, decision, *, allowed, validate, copy=deepcopy):
    """선언 필드만 교체하고 전체 후보를 검증한다. 호출자 사본/승인/영수증은 변경하지 않는다."""
    changes = decision.changes or {}
    unknown = changes.keys() - set(allowed)
    if unknown:
        raise ValueError(RetrySelection.UNDECLARED_CHANGES.format(fields=sorted(unknown)))
    result = copy(arguments)
    result.update(copy(changes))
    validate(result)
    return result
