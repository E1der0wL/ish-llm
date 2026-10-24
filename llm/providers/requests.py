"""안전하게 반복 가능한 비스트리밍 추론 호출의 제한·오류 분류. Tool에는 적용하지 않는다."""

from llm.providers.diagnostic_values import ProviderRetryObservation, ProviderDiagnosticText, ProviderAttempt

from llm.core.model_protocol import copy_model

from .codes import ProviderErrorCode
from .error_values import SDKError
from .response_values import SDKCompletionResponse
from .request_values import SDKOperation
from .options import ProviderOptions
import asyncio
import time
from uuid import uuid4

from .observations import observed_call
from .runtime import diagnostic, diagnostic_failure, diagnostic_context, request_details
from .retry import effective_attempts, retry_delay
from llm.errors import CodedError, exception_chain, stable_error_code
from llm.policies.retry import RetryBackoff, RetryPolicy, RetryFailure, select_retry, change_arguments


def provider_schema():
    return ProviderOptions.describe_config()


def resolve_provider_options(options):
    return ProviderOptions.resolve(options)


class ProviderError(CodedError, RuntimeError):
    NO_DIAGNOSTIC = None

    def __init__(self, code, *, diagnostic=NO_DIAGNOSTIC):
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(ProviderDiagnosticText.describe(code))


class ProviderFailure:
    """Stable failure classification; observation alone does not permit a retry."""

    UNSPECIFIED = None
    AUTHENTICATION_STATUS = (401, 403)
    RATE_LIMIT_STATUS = 429
    TIMEOUT_STATUS = 408
    CLIENT_ERROR_START = 400
    SERVER_ERROR_START = 500
    SERVER_ERROR_END = 600
    SYNTHETIC_INVALID = "empty or invalid response from llm endpoint"
    INVALID_RESPONSE = "empty or invalid response"
    MISSING_RESPONSE = "received: none"
    TIMEOUT_NAME = "timeout"
    CONNECTION_NAMES = ("apiconnectionerror", "connecterror", "readerror", "remoteprotocolerror")
    CODE_PREFIX = "provider_"
    TRANSIENT = {
        ProviderErrorCode.TIMEOUT.value,
        ProviderErrorCode.CONNECTION.value,
        ProviderErrorCode.RATE_LIMIT.value,
        ProviderErrorCode.UNAVAILABLE.value,
        ProviderErrorCode.EMPTY_RESPONSE.value,
        ProviderErrorCode.INVALID_RESPONSE.value,
    }

    @classmethod
    def classify(cls, error):
        """오류 텍스트는 분류에만 사용한다. 원문은 로그에 넣지 않는다."""
        known = stable_error_code(error)
        if known is not cls.UNSPECIFIED:
            return known
        for error in exception_chain(error):
            if isinstance(error, asyncio.CancelledError):
                return ProviderErrorCode.CANCELLED.value
            status = SDKError(error).status
            if status in cls.AUTHENTICATION_STATUS:
                return ProviderErrorCode.AUTHENTICATION.value
            if status == cls.RATE_LIMIT_STATUS:
                return ProviderErrorCode.RATE_LIMIT.value
            if status == cls.TIMEOUT_STATUS:
                return ProviderErrorCode.TIMEOUT.value
            # LiteLLM이 빈/잘못된 응답에 붙이는 synthetic 500만 따로 분류한다.
            # 실제 서버 500과 나머지 5xx는 계속 unavailable로 유지한다.
            text = str(error).lower()
            if status == cls.SERVER_ERROR_START and cls.SYNTHETIC_INVALID in text:
                return ProviderErrorCode.INVALID_RESPONSE.value
            if isinstance(status, int) and cls.SERVER_ERROR_START <= status < cls.SERVER_ERROR_END:
                return ProviderErrorCode.UNAVAILABLE.value
            if isinstance(status, int) and cls.CLIENT_ERROR_START <= status < cls.SERVER_ERROR_START:
                return ProviderErrorCode.INVALID_REQUEST.value
            name = type(error).__name__.lower()
            if cls.INVALID_RESPONSE in text or cls.MISSING_RESPONSE in text:
                return ProviderErrorCode.INVALID_RESPONSE.value
            if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or cls.TIMEOUT_NAME in name:
                return ProviderErrorCode.TIMEOUT.value
            if isinstance(error, ConnectionError) or name in cls.CONNECTION_NAMES:
                return ProviderErrorCode.CONNECTION.value
        return ProviderErrorCode.FAILED.value

    @classmethod
    def owns_code(cls, code):
        return code.startswith(cls.CODE_PREFIX)


def error_code(error):
    return ProviderFailure.classify(error)


TRANSIENT = ProviderFailure.TRANSIENT


class ProviderRequestExecution:
    UNSPECIFIED = None
    REQUIRE_MODEL = True
    RUNTIME_CONTRACT = False
    SDK_DEFAULTS = False
    INITIALIZE = "initialize"
    INVOKE = "invoke"


async def request_model(
    operation,
    request,
    options,
    *,
    call=ProviderRequestExecution.UNSPECIFIED,
    deadline=ProviderRequestExecution.UNSPECIFIED,
    decide=ProviderRequestExecution.UNSPECIFIED,
    changeable=(),
):
    """SDK 선택/초기화는 Provider 내부에서만 한다. 상위 모델 클라이언트는 호출 계약만 사용한다."""
    from .schema import validate_model_params

    options = resolve_provider_options(options)
    provider_policy = ProviderOptions(options)
    deadline = RetryPolicy.end(provider_policy.wall_timeout, deadline)
    validate_model_params(
        request,
        operation=operation,
        require_model=ProviderRequestExecution.REQUIRE_MODEL,
        json_contract=ProviderRequestExecution.RUNTIME_CONTRACT,
    )
    sdk_defaults = call is ProviderRequestExecution.UNSPECIFIED
    call_id = uuid4().hex
    if call is ProviderRequestExecution.UNSPECIFIED:
        from .litellm import resolve_call

        with diagnostic_context(call_id=call_id, **request_details(request)):
            try:
                async with asyncio.timeout(RetryPolicy(deadline=deadline).remaining()):
                    call = await resolve_call(operation, request)
            except Exception as error:
                failure = diagnostic_failure(
                    error, operation=operation, stage=ProviderRequestExecution.INITIALIZE
                )
                raise ProviderError(error_code(error), diagnostic=failure) from error
    return await invoke(
        operation,
        request,
        call,
        options,
        deadline=deadline,
        sdk_defaults=sdk_defaults,
        decide=decide,
        changeable=changeable,
        call_id=call_id,
    )


async def invoke(
    operation,
    request,
    call,
    options,
    *,
    deadline=ProviderRequestExecution.UNSPECIFIED,
    sdk_defaults=ProviderRequestExecution.SDK_DEFAULTS,
    decide=ProviderRequestExecution.UNSPECIFIED,
    changeable=(),
    call_id=ProviderRequestExecution.UNSPECIFIED,
):
    """각 실제 시도를 관찰한다. 전체 deadline에는 대기·backoff·사용량 관찰도 포함된다."""
    options = resolve_provider_options(options)
    provider_policy = ProviderOptions(options)
    started = time.monotonic()
    end = RetryPolicy.end(provider_policy.wall_timeout, deadline)
    attempts = effective_attempts(
        request, RetryPolicy(provider_policy.max_attempts).attempts, sdk_defaults=sdk_defaults
    )
    policy = RetryPolicy(
        attempts,
        provider_policy.delay_seconds,
        provider_policy.max_delay_seconds,
        end,
        RetryBackoff.EXPONENTIAL,
    )
    request = copy_model(request)
    call_id = call_id or uuid4().hex
    for attempt in range(attempts):
        observation = ProviderAttempt.from_index(call_id, attempt)
        details = ProviderAttempt.call_details(call_id, request_details(request))
        try:
            remaining = policy.remaining()
        except TimeoutError as error:
            raise ProviderError(ProviderErrorCode.TIMEOUT.value) from error
        try:
            # 취소는 BaseException이므로 retry되지 않는다. 각 시도에 가변 인자 사본을 전달한다.
            async def checked(**params):
                from .calls import current_calls

                admission = current_calls()
                if admission is not ProviderRequestExecution.UNSPECIFIED:
                    await admission.acquire()
                try:
                    result = await call(**params)
                finally:
                    if admission is not ProviderRequestExecution.UNSPECIFIED:
                        admission.release()
                if result is ProviderRequestExecution.UNSPECIFIED:
                    raise ProviderError(ProviderErrorCode.EMPTY_RESPONSE.value)
                if operation == SDKOperation.ASYNC_COMPLETION:
                    choices = SDKCompletionResponse(result).choices
                    if not isinstance(choices, list) or not choices:
                        raise ProviderError(ProviderErrorCode.INVALID_RESPONSE.value)
                    content = SDKCompletionResponse.message(choices[0]).content
                    if (
                        content is ProviderRequestExecution.UNSPECIFIED
                        or isinstance(content, str)
                        and not content.strip()
                    ):
                        raise ProviderError(ProviderErrorCode.EMPTY_RESPONSE.value)
                    if not isinstance(content, str):
                        raise ProviderError(ProviderErrorCode.INVALID_RESPONSE.value)
                return result

            with diagnostic_context(**details, attempt=observation.attempt):
                from .litellm import describe_call

                return await asyncio.wait_for(
                    observed_call(describe_call(operation, request), copy_model(request), checked), remaining
                )
        except Exception as error:
            code = error_code(error)
            failure = diagnostic_failure(
                error,
                operation=operation,
                stage=ProviderRequestExecution.INVOKE,
                attempt=observation.attempt,
                **details,
                elapsed_seconds=time.monotonic() - started,
            )
            correctable = (
                code == ProviderErrorCode.INVALID_REQUEST.value
                and decide is not ProviderRequestExecution.UNSPECIFIED
                and bool(changeable)
            )
            if (code not in TRANSIENT and not correctable) or not policy.allows(observation.attempt):
                if (
                    code == ProviderErrorCode.FAILED.value and not sdk_defaults
                ) or not ProviderFailure.owns_code(code):
                    raise
                raise ProviderError(code, diagnostic=failure) from error
            try:
                from .schema import validate_retry_params

                async with asyncio.timeout(policy.remaining()):
                    selected = await select_retry(
                        RetryFailure(
                            operation,
                            copy_model(request),
                            code,
                            ProviderDiagnosticText.describe(code),
                            observation.attempt,
                            code in TRANSIENT,
                            tuple(changeable),
                        ),
                        decide,
                    )
                if selected is ProviderRequestExecution.UNSPECIFIED:
                    raise ProviderError(code, diagnostic=failure) from error
                request = change_arguments(
                    request,
                    selected,
                    allowed=changeable,
                    copy=copy_model,
                    validate=lambda value: validate_retry_params(
                        value, original=request, operation=operation
                    ),
                )
                delay = retry_delay(policy, observation.attempt, error, options)
                if delay is ProviderRequestExecution.UNSPECIFIED:
                    diagnostic(
                        ProviderRetryObservation.STOPPED,
                        operation=operation,
                        attempt=observation.attempt,
                        call_id=call_id,
                        reason=ProviderRetryObservation.EXCEEDED_DELAY,
                    )
                    raise ProviderError(code, diagnostic=failure) from error
            except TimeoutError as timeout_error:
                raise ProviderError(ProviderErrorCode.TIMEOUT.value) from timeout_error
            diagnostic(
                ProviderRetryObservation.RETRY,
                operation=operation,
                attempt=observation.next_attempt,
                call_id=call_id,
                delay_seconds=delay,
                modified=bool(selected.changes),
            )
            from .observations import record_retry

            record_retry(operation)
            await asyncio.sleep(delay)
