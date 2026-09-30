"""응답을 받기 전의 일시적 공급자 오류만 재시도한다. Tool 효과에는 적용하지 않는다."""

from contextlib import contextmanager
from contextvars import ContextVar
import sys

_policy = ContextVar("llm_provider_retry", default={})


def sdk_retry_enabled(request, *, sdk_defaults=False):
    """인자/클라이언트/SDK 기본값을 읽기만 한다. 불명확한 SDK 정책에는 외부 재시도를 겹치지 않는다."""
    def enabled(value):
        return value is not None and (type(value) not in (int, float) or value > 0)

    if any(enabled(request.get(key)) for key in ("num_retries", "max_retries")):
        return True
    if request.get("retry_policy") is not None:
        return True  # 공급자별 예외 정책을 이 계층에서 다시 해석하지 않는다.
    client = request.get("client")
    if client is not None and enabled(getattr(client, "max_retries", None)):
        return True
    if sdk_defaults:
        sdk = sys.modules.get("litellm")
        if sdk is None:
            return True  # 지연 import 이전에는 기본값을 알 수 없다.
        # 일부 SDK 경로는 명시적 0도 DEFAULT_MAX_RETRIES로 되돌린다.
        # 전역값을 바꾸지 않고 활성 기본값이 있으면 보수적으로 한 번만 호출한다.
        if enabled(getattr(sdk, "num_retries", None)):
            return True
        default = getattr(sdk, "DEFAULT_MAX_RETRIES", None)
        return default is None or enabled(default)
    return False


def effective_attempts(request, maximum, *, sdk_defaults=False):
    attempts = 1 if sdk_retry_enabled(request, sdk_defaults=sdk_defaults) else maximum
    if attempts < maximum:
        from .runtime import diagnostic
        diagnostic("provider_retry_delegated", model=request.get("model"),
                   configured_attempts=maximum, effective_attempts=attempts)
    return attempts


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
    from .requests import error_code, TRANSIENT
    return error_code(error) in TRANSIENT
