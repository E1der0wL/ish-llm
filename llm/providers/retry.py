"""응답을 받기 전의 일시적 공급자 오류만 재시도한다. Tool 효과에는 적용하지 않는다."""

import sys
import math
import time
from email.utils import parsedate_to_datetime

from llm.errors import exception_chain
from llm.policies.retry import RetryPolicy

SDK_RETRY_FIELDS = ("num_retries", "max_retries")


def sdk_transport_request(request, sdk):
    """Disable unselected SDK retries in a transport copy, preserving explicit policy."""
    from llm.core.model_protocol import copy_model
    result = copy_model(request)
    # A host's explicit SDK policy remains SDK-owned, just like a request policy.
    # LiteLLM also maps num_retries onto OpenAI's max_retries. Adding a zero
    # beside an explicit max_retries/client policy would override that choice.
    if ("num_retries" not in result and "max_retries" not in result
            and result.get("client") is None and result.get("retry_policy") is None
            and getattr(sdk, "num_retries", None) is None):
        result["num_retries"] = 0
    if "max_retries" not in result:
        client = result.get("client")
        # LiteLLM writes this argument back to injected OpenAI clients. Preserve
        # their selected value; None leaves clients without this field untouched.
        result["max_retries"] = 0 if client is None else getattr(client, "max_retries", None)
    return result


def retry_after(error):
    """SDK 응답의 Retry-After만 해석한다. 잘못된 값은 정책/기한을 변경하지 않는다."""
    for current in exception_chain(error):
        response = getattr(current, "response", None)
        headers = getattr(response, "headers", None) or getattr(current, "headers", None)
        if not hasattr(headers, "items"):
            continue
        value = next((v for k, v in headers.items() if str(k).lower() == "retry-after"), None)
        if value is None:
            continue
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    continue
                seconds = max(0., date.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                continue
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    return None


def retry_delay(policy, attempt, error, options):
    """명시된 서버 대기/jitter만 적용한다. 서버 대기가 설정 상한보다 크면 재시도를 포기한다."""
    minimum = retry_after(error) if options.get("respect_retry_after") is True else None
    if minimum is not None and policy.max_delay_seconds is not None and minimum > policy.max_delay_seconds:
        return None
    return policy.delay(attempt, minimum=minimum, jitter=options.get("jitter") is True)

def sdk_retry_enabled(request, *, sdk_defaults=False):
    """인자/클라이언트/SDK 기본값을 읽기만 한다. 불명확한 SDK 정책에는 외부 재시도를 겹치지 않는다."""
    def enabled(value):
        return value is not None and (type(value) not in (int, float) or value > 0)

    if any(enabled(request.get(key)) for key in SDK_RETRY_FIELDS):
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
    attempts = (RetryPolicy.INITIAL_ATTEMPTS
                if sdk_retry_enabled(request, sdk_defaults=sdk_defaults) else maximum)
    if attempts < maximum:
        from .runtime import diagnostic
        diagnostic("provider_retry_delegated", model=request.get("model"),
                   configured_attempts=maximum, effective_attempts=attempts)
    return attempts


def transient(error):
    from .requests import error_code, TRANSIENT
    return error_code(error) in TRANSIENT
