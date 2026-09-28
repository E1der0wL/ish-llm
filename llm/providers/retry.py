"""응답을 받기 전의 일시적 공급자 오류만 재시도한다. Tool 효과에는 적용하지 않는다."""

from contextlib import contextmanager
from contextvars import ContextVar

_policy = ContextVar("llm_provider_retry", default={})


@contextmanager
def retry_scope(settings):
    token = _policy.set(settings)
    try:
        yield
    finally:
        _policy.reset(token)


def retry_settings():
    return _policy.get()


def transient(error):
    visited = set()
    while error is not None and id(error) not in visited:
        visited.add(id(error))
        if getattr(error, "status_code", None) in (429, 502, 503, 504):
            return True
        error = error.__cause__
    return False
