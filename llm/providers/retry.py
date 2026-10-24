"""응답을 받기 전의 일시적 공급자 오류만 재시도한다. Tool 효과에는 적용하지 않는다."""

import math
import time
from email.utils import parsedate_to_datetime
from llm.errors import exception_chain
from llm.policies.retry import RetryPolicy
from .options import ProviderOptions
from .request_values import SDKRetryParameters, ModelParameters
from .error_values import SDKError
from .diagnostic_values import ProviderRetryObservation

SDK_RETRY_FIELDS = SDKRetryParameters.RETRY_FIELDS


class RetryAfter:
    """Interpret only the server header, without changing the selected deadline."""

    HEADER = "retry-after"
    ITEMS = SDKError.HEADER_ITEMS
    MINIMUM_SECONDS = 0.0
    UNSPECIFIED = None

    @classmethod
    def read(cls, error):
        """SDK 응답의 Retry-After만 해석한다. 잘못된 값은 정책/기한을 변경하지 않는다."""
        for current in exception_chain(error):
            headers = SDKError(current).headers
            if not hasattr(headers, cls.ITEMS):
                continue
            value = next((v for k, v in headers.items() if str(k).lower() == cls.HEADER), cls.UNSPECIFIED)
            if value is cls.UNSPECIFIED:
                continue
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                try:
                    date = parsedate_to_datetime(value)
                    if date.tzinfo is cls.UNSPECIFIED:
                        continue
                    seconds = max(cls.MINIMUM_SECONDS, date.timestamp() - time.time())
                except (TypeError, ValueError, OverflowError):
                    continue
            if math.isfinite(seconds) and seconds >= cls.MINIMUM_SECONDS:
                return seconds
        return cls.UNSPECIFIED


def sdk_transport_request(request, sdk):
    """Disable unselected SDK retries in a transport copy, preserving explicit policy."""
    return SDKRetryParameters(request).transport_copy(sdk)


def retry_after(error):
    return RetryAfter.read(error)


def retry_delay(policy, attempt, error, options):
    """명시된 서버 대기/jitter만 적용한다. 서버 대기가 설정 상한보다 크면 재시도를 포기한다."""
    selected = ProviderOptions(options)
    minimum = retry_after(error) if selected.respect_retry_after else RetryAfter.UNSPECIFIED
    if (
        minimum is not RetryAfter.UNSPECIFIED
        and policy.max_delay_seconds is not RetryAfter.UNSPECIFIED
        and minimum > policy.max_delay_seconds
    ):
        return RetryAfter.UNSPECIFIED
    return policy.delay(attempt, minimum=minimum, jitter=selected.jitter)


def sdk_retry_enabled(request, *, sdk_defaults=SDKRetryParameters.NOT_ENABLED):
    """Observe the selected SDK policy without overlaying Backend retries."""
    return SDKRetryParameters(request).selects_retries(sdk_defaults=sdk_defaults)


def effective_attempts(request, maximum, *, sdk_defaults=SDKRetryParameters.NOT_ENABLED):
    attempts = (
        RetryPolicy.INITIAL_ATTEMPTS if sdk_retry_enabled(request, sdk_defaults=sdk_defaults) else maximum
    )
    if attempts < maximum:
        from .runtime import diagnostic

        diagnostic(
            ProviderRetryObservation.DELEGATED,
            model=ModelParameters(request).optional_model,
            configured_attempts=maximum,
            effective_attempts=attempts,
        )
    return attempts


def transient(error):
    from .requests import error_code, TRANSIENT

    return error_code(error) in TRANSIENT
