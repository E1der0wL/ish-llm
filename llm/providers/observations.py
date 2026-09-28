"""비스트리밍 모델 호출의 선택적 관찰 경계. 저장 위치와 도메인 수명은 알지 않는다."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

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
    return await observer(operation, request, call) if observer else await call(**request)


def observe_component_models(method):
    """ComponentData가 제공하는 사용량 관찰자를 해당 비동기 작업에만 연결한다."""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        with self.model_scope():
            return await method(self, *args, **kwargs)
    return wrapped
