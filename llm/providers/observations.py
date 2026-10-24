"""비스트리밍 모델 호출의 선택적 관찰 경계. 저장 위치와 도메인 수명은 알지 않는다."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from dataclasses import dataclass
from typing import ClassVar
from collections.abc import Callable
import asyncio
import time

_observer = ContextVar("llm_model_observer", default=None)


class ModelObservationContract:
    ATTRIBUTE = "observes_model_calls"
    UNSUPPORTED = False
    SUPPORTED = True

    @classmethod
    def supported(cls, client):
        return getattr(client, cls.ATTRIBUTE, cls.UNSUPPORTED) is cls.SUPPORTED


@dataclass(frozen=True, slots=True)
class ModelCall:
    """Adapter-owned usage interpretation; services own quotas and persistence.

    count_tokens(counter) returns a conservative input+output reservation, or
    raises when it cannot be determined. read_usage(response) returns canonical
    usage fields (including total_tokens when known). No SDK objects are stored.
    These trusted callbacks grant no retry, approval or execution authority.
    """
    INVALID_OPERATION: ClassVar[str] = "Model call requires an operation"
    INVALID_MODEL: ClassVar[str] = "Model identity must be text or None"
    INVALID_CALLBACKS: ClassVar[str] = "Model call requires usage interpretation callbacks"
    INVALID_INVOCATION: ClassVar[str] = "Model observation requires an adapter-owned ModelCall"

    operation: str
    model: str | None
    count_tokens: Callable
    read_usage: Callable
    classify_error: Callable

    def __post_init__(self):
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError(self.INVALID_OPERATION)
        if self.model is not None and not isinstance(self.model, str):
            raise TypeError(self.INVALID_MODEL)
        if not all(callable(fn) for fn in (self.count_tokens, self.read_usage, self.classify_error)):
            raise TypeError(self.INVALID_CALLBACKS)


@contextmanager
def model_observer(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


async def observed_call(invocation: ModelCall, request, call):
    if not isinstance(invocation, ModelCall):
        raise TypeError(ModelCall.INVALID_INVOCATION)
    observer = _observer.get()
    async def tracked(**params):
        with provider_attempt(invocation):
            return await call(**params)
    return await observer(invocation, request, tracked) if observer else await tracked(**request)


@contextmanager
def provider_attempt(invocation: ModelCall):
    """공통 SDK 호출 경계. SDK 내부 retry 횟수는 관찰할 수 없으므로 추측하지 않는다."""
    from llm.services.infrastructure.observability import record, ObservationKind, ObservationStatus
    operation = invocation.operation
    started, status, code = time.monotonic(), ObservationStatus.COMPLETED.value, None
    record(ObservationKind.PROVIDERS.value, ObservationStatus.CALLS.value, name=operation)
    try:
        yield
    except asyncio.CancelledError:
        status = ObservationStatus.CANCELLED.value
        raise
    except BaseException as error:
        status, code = ObservationStatus.FAILED.value, invocation.classify_error(error)
        raise
    finally:
        record(ObservationKind.PROVIDERS.value, status, code=code, name=operation, duration_seconds=time.monotonic() - started)


def record_retry(operation):
    from llm.services.infrastructure.observability import record, ObservationKind, ObservationStatus
    record(ObservationKind.PROVIDERS.value, ObservationStatus.RETRIES.value, name=operation)


def observe_component_models(method):
    """ComponentData가 제공하는 사용량 관찰자를 해당 비동기 작업에만 연결한다."""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        with self.model_scope():
            return await method(self, *args, **kwargs)
    return wrapped
