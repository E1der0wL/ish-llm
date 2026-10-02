"""비스트리밍 모델 호출의 선택적 관찰 경계. 저장 위치와 도메인 수명은 알지 않는다."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import asyncio
import time

_observer = ContextVar("llm_model_observer", default=None)


@contextmanager
def model_observer(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


async def observed_call(operation, request, call):
    observer = _observer.get()
    async def tracked(**params):
        with provider_attempt(operation):
            return await call(**params)
    return await observer(operation, request, tracked) if observer else await tracked(**request)


@contextmanager
def provider_attempt(operation):
    """공통 SDK 호출 경계. SDK 내부 retry 횟수는 관찰할 수 없으므로 추측하지 않는다."""
    from llm.services.infrastructure.observability import record
    started, status, code = time.monotonic(), "completed", None
    record("providers", "calls", name=operation)
    try:
        yield
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except BaseException as error:
        from .requests import error_code
        status, code = "failed", error_code(error)
        raise
    finally:
        record("providers", status, code=code, name=operation, duration_seconds=time.monotonic() - started)


def record_retry(operation):
    from llm.services.infrastructure.observability import record
    record("providers", "retries", name=operation)


def observe_component_models(method):
    """ComponentData가 제공하는 사용량 관찰자를 해당 비동기 작업에만 연결한다."""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        with self.model_scope():
            return await method(self, *args, **kwargs)
    return wrapped
