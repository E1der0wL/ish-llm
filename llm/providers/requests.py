"""안전하게 반복 가능한 비스트리밍 추론 호출의 제한·오류 분류. Tool에는 적용하지 않는다."""

import asyncio
import time
import math
from uuid import uuid4

from .observations import observed_call
from .parameters import copy_params
from .runtime import diagnostic, diagnostic_failure, diagnostic_context, request_details
from .retry import effective_attempts, retry_delay
from llm.errors import CodedError, exception_chain
from llm.policies.retry import RetryPolicy, RetryFailure, select_retry, change_arguments


def provider_schema():
    from llm.core.schema import object_schema, field
    return object_schema({
        "max_attempts": field("integer", minimum=1, **{"x-narrowing": "maximum"}),
        "wall_timeout": field(["number", "null"], exclusiveMinimum=0, **{"x-narrowing": "maximum"}),
        "delay_seconds": field("number", minimum=0),
        "max_delay_seconds": field("number", minimum=0),
        "respect_retry_after": field("boolean", description="서버 Retry-After를 최소 대기로 존중. 대기 상한을 넘으면 재시도 중단"),
        "jitter": field("boolean", description="명시된 백오프 범위에서 대기를 분산. Retry-After 최소 대기는 유지"),
    }, additionalProperties=False)


def resolve_provider_options(options):
    from jsonschema import Draft202012Validator
    result = dict(options)
    Draft202012Validator(provider_schema()).validate(result)
    if "max_attempts" in result and type(result["max_attempts"]) is not int:
        raise ValueError("Provider max_attempts requires an integer")
    if any(result.get(key) is not None and not math.isfinite(result[key]) for key in ("wall_timeout", "delay_seconds", "max_delay_seconds")):
        raise ValueError("Provider timing configuration must be finite")
    return result


class ProviderError(CodedError, RuntimeError):
    def __init__(self, code, *, diagnostic=None):
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(code.replace("_", " "))


def error_code(error):
    """오류 텍스트는 분류에만 사용한다. 원문은 로그에 넣지 않는다."""
    for error in exception_chain(error):
        if isinstance(error, asyncio.CancelledError):
            return "provider_cancelled"
        code = getattr(error, "code", None)
        if isinstance(error, CodedError) and isinstance(code, str) and code.strip():
            return code
        status = getattr(error, "status_code", None)
        if status in (401, 403):
            return "provider_authentication"
        if status == 429:
            return "provider_rate_limit"
        if status == 408:
            return "provider_timeout"
        # LiteLLM이 빈/잘못된 응답에 붙이는 synthetic 500만 따로 분류한다.
        # 실제 서버 500과 나머지 5xx는 계속 unavailable로 유지한다.
        text = str(error).lower()
        if status == 500 and "empty or invalid response from llm endpoint" in text:
            return "provider_invalid_response"
        if isinstance(status, int) and 500 <= status < 600:
            return "provider_unavailable"
        if isinstance(status, int) and 400 <= status < 500:
            return "provider_invalid_request"
        name = type(error).__name__.lower()
        if "empty or invalid response" in text or "received: none" in text:
            return "provider_invalid_response"
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in name:
            return "provider_timeout"
        if isinstance(error, ConnectionError) or name in ("apiconnectionerror", "connecterror", "readerror", "remoteprotocolerror"):
            return "provider_connection"
    return "provider_failed"


TRANSIENT = {"provider_timeout", "provider_connection", "provider_rate_limit", "provider_unavailable",
             "provider_empty_response", "provider_invalid_response"}


async def request_model(operation, request, options, *, call=None, deadline=None,
                        decide=None, changeable=()):
    """SDK 선택/초기화는 Provider 내부에서만 한다. 상위 모델 클라이언트는 호출 계약만 사용한다."""
    from .schema import validate_model_params
    options = resolve_provider_options(options)
    deadline = RetryPolicy.end(options.get("wall_timeout"), deadline)
    validate_model_params(request, operation=operation, require_model=True, json_contract=False)
    sdk_defaults = call is None
    call_id = uuid4().hex
    if call is None:
        from .litellm import resolve_call
        with diagnostic_context(call_id=call_id, **request_details(request)):
            try:
                async with asyncio.timeout(RetryPolicy(deadline=deadline).remaining()):
                    call = await resolve_call(operation, request)
            except Exception as error:
                failure = diagnostic_failure(error, operation=operation, stage="initialize")
                raise ProviderError(error_code(error), diagnostic=failure) from error
    return await invoke(operation, request, call, options, deadline=deadline,
                        sdk_defaults=sdk_defaults, decide=decide, changeable=changeable, call_id=call_id)


async def invoke(operation, request, call, options, *, deadline=None, sdk_defaults=False,
                 decide=None, changeable=(), call_id=None):
    """각 실제 시도를 관찰한다. 전체 deadline에는 대기·backoff·사용량 관찰도 포함된다."""
    options = resolve_provider_options(options)
    started = time.monotonic()
    end = RetryPolicy.end(options.get("wall_timeout"), deadline)
    attempts = effective_attempts(request, options.get("max_attempts", 1), sdk_defaults=sdk_defaults)
    policy = RetryPolicy(attempts, options.get("delay_seconds"), options.get("max_delay_seconds"), end, "exponential")
    request = copy_params(request)
    call_id = call_id or uuid4().hex
    for attempt in range(attempts):
        details = {"call_id": call_id, **request_details(request)}
        try:
            remaining = policy.remaining()
        except TimeoutError as error:
            raise ProviderError("provider_timeout") from error
        try:
            # 취소는 BaseException이므로 retry되지 않는다. 각 시도에 가변 인자 사본을 전달한다.
            async def checked(**params):
                from .calls import current_calls
                admission = current_calls()
                if admission is not None:
                    await admission.acquire()
                try:
                    result = await call(**params)
                finally:
                    if admission is not None:
                        admission.release()
                if result is None:
                    raise ProviderError("provider_empty_response")
                if operation == "acompletion":
                    from .embeddings import value
                    choices = value(result, "choices")
                    if not isinstance(choices, list) or not choices:
                        raise ProviderError("provider_invalid_response")
                    content = value(value(choices[0], "message"), "content")
                    if content is None or isinstance(content, str) and not content.strip():
                        raise ProviderError("provider_empty_response")
                    if not isinstance(content, str):
                        raise ProviderError("provider_invalid_response")
                return result
            with diagnostic_context(**details, attempt=attempt + 1):
                return await asyncio.wait_for(observed_call(operation, copy_params(request), checked), remaining)
        except Exception as error:
            code = error_code(error)
            failure = diagnostic_failure(error, operation=operation, stage="invoke", attempt=attempt + 1,
                               **details, elapsed_seconds=time.monotonic() - started)
            correctable = code == "provider_invalid_request" and decide is not None and bool(changeable)
            if (code not in TRANSIENT and not correctable) or not policy.allows(attempt + 1):
                if (code == "provider_failed" and not sdk_defaults) or not code.startswith("provider_"):
                    raise
                raise ProviderError(code, diagnostic=failure) from error
            try:
                from .schema import validate_retry_params
                async with asyncio.timeout(policy.remaining()):
                    selected = await select_retry(RetryFailure(operation, copy_params(request), code,
                        code.replace("_", " "), attempt + 1, code in TRANSIENT, tuple(changeable)), decide)
                if selected is None:
                    raise ProviderError(code, diagnostic=failure) from error
                request = change_arguments(request, selected, allowed=changeable, copy=copy_params,
                    validate=lambda value: validate_retry_params(value, original=request, operation=operation))
                delay = retry_delay(policy, attempt + 1, error, options)
                if delay is None:
                    diagnostic("provider_retry_stopped", operation=operation, attempt=attempt + 1,
                               call_id=call_id, reason="retry_after_exceeds_max_delay")
                    raise ProviderError(code, diagnostic=failure) from error
            except TimeoutError as timeout_error:
                raise ProviderError("provider_timeout") from timeout_error
            diagnostic("provider_retry", operation=operation, attempt=attempt + 2,
                       call_id=call_id, delay_seconds=delay, modified=bool(selected.changes))
            from .observations import record_retry
            record_retry(operation)
            await asyncio.sleep(delay)
