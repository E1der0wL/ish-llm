"""응답을 받기 전의 일시적 공급자 오류만 재시도한다. Tool 효과에는 적용하지 않는다."""

import sys

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
        # Runtime은 import 전/호출 진입마다 DEFAULT_MAX_RETRIES=0을 보장한다.
        # 별도로 설정한 호스트의 num_retries는 명시적 SDK 정책으로 존중한다.
        if sdk is not None and enabled(getattr(sdk, "num_retries", None)):
            return True
    return False


def effective_attempts(request, maximum, *, sdk_defaults=False):
    attempts = 1 if sdk_retry_enabled(request, sdk_defaults=sdk_defaults) else maximum
    if attempts < maximum:
        from .runtime import diagnostic
        diagnostic("provider_retry_delegated", model=request.get("model"),
                   configured_attempts=maximum, effective_attempts=attempts)
    return attempts


def transient(error):
    from .requests import error_code, TRANSIENT
    return error_code(error) in TRANSIENT
