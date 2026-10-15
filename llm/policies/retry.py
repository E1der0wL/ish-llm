"""논리 작업의 재시도 계산과 Application 선택 계약. 오류/효과/저장은 호출자가 소유한다."""

import asyncio
import inspect
import math
import random
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """재시도 선택. changes=None/{}는 같은 인자, dict는 선언된 최상위 필드 교체다.

    선택기가 None을 반환하면 중단한다. null 값은 삭제가 아니라 명시적 값이다.
    """

    changes: dict | None = None

    def __post_init__(self):
        if self.changes is not None and (not isinstance(self.changes, dict)
                or any(not isinstance(k, str) for k in self.changes)):
            raise TypeError("Retry changes must be a string-keyed object")
        object.__setattr__(self, "changes", deepcopy(self.changes))


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

    max_attempts: int | None = None
    delay_seconds: float | None = None
    max_delay_seconds: float | None = None
    deadline: float | None = None
    backoff: str = "fixed"

    def __post_init__(self):
        if self.max_attempts is not None and (type(self.max_attempts) is not int or self.max_attempts < 1):
            raise ValueError("Retry max_attempts must be a positive integer")
        for value in (self.delay_seconds, self.max_delay_seconds):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("Retry delay must be finite and nonnegative")
        if self.deadline is not None and (type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)):
            raise ValueError("Retry deadline must be finite")
        if self.backoff not in ("fixed", "exponential"):
            raise ValueError("Unknown retry backoff")

    @staticmethod
    def end(timeout=None, deadline=None):
        """이미 존재하는 상위 절대 기한을 넓히지 않는다."""
        end = None if timeout is None else time.monotonic() + timeout
        return deadline if end is None else end if deadline is None else min(end, deadline)

    def allows(self, attempt: int) -> bool:
        """attempt는 완료/실패한 호출의 1-based 번호다."""
        return self.max_attempts is not None and attempt < self.max_attempts

    def remaining(self):
        if self.deadline is None:
            return None
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("Retry deadline exceeded")
        return value

    def delay(self, attempt: int, *, minimum: float | None = None, jitter: bool = False) -> float:
        """대기 가능한 간격을 계산한다. 기한 밖의 재시도를 관찰자에게 예약했다고 알리지 않는다."""
        value = self.delay_seconds if self.delay_seconds is not None else 0
        if value and self.backoff == "exponential":
            # 큰 지수도 overflow로 작업 경계를 깨뜨리지 않게 cap/기한에서 처리한다.
            try:
                value = math.ldexp(value, attempt - 1)
            except OverflowError:
                value = math.inf
        if self.max_delay_seconds is not None:
            value = min(value, self.max_delay_seconds)
        if jitter and math.isfinite(value):
            value = random.uniform(0, value)
        if minimum is not None:
            if not math.isfinite(minimum) or minimum < 0:
                raise ValueError("Retry minimum delay must be finite and nonnegative")
            value = max(value, minimum)
        remaining = self.remaining()
        if remaining is not None and value >= remaining:
            raise TimeoutError("Retry deadline exceeded")
        if not math.isfinite(value):
            raise ValueError("Retry delay is not finite; configure max_delay_seconds")
        return value

    async def wait(self, attempt: int):
        await asyncio.sleep(self.delay(attempt))


async def select_retry(failure: RetryFailure, decide: Callable | None = None):
    """Application이 선택한다. 선택기가 없으면 기존 명시적 동일 인자 retry만 유지한다."""
    if decide is None:
        return RetryDecision() if failure.retryable else None
    result = decide(failure)
    if inspect.isawaitable(result):
        result = await result
    if result is not None and not isinstance(result, RetryDecision):
        raise TypeError("Retry selector must return RetryDecision or None")
    if result is not None and not result.changes and not failure.retryable:
        raise ValueError("This failure requires corrected arguments, not an unchanged retry")
    return result


def change_arguments(arguments, decision, *, allowed, validate, copy=deepcopy):
    """선언 필드만 교체하고 전체 후보를 검증한다. 호출자 사본/승인/영수증은 변경하지 않는다."""
    changes = decision.changes or {}
    unknown = changes.keys() - set(allowed)
    if unknown:
        raise ValueError(f"Retry cannot change arguments: {sorted(unknown)}")
    result = copy(arguments)
    result.update(copy(changes))
    validate(result)
    return result
