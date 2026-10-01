"""안전하게 반복 가능한 비스트리밍 추론 호출의 제한·오류 분류. Tool에는 적용하지 않는다."""

import asyncio
import time
import math

from .observations import observed_call
from .parameters import copy_params
from .runtime import diagnostic
from .retry import effective_attempts


def provider_schema():
    from llm.core.schema import object_schema, field
    return object_schema({
        "max_attempts": field("integer", minimum=1, maximum=10),
        "wall_timeout": field(["number", "null"], exclusiveMinimum=0),
        "delay_seconds": field("number", minimum=0),
        "max_delay_seconds": field("number", minimum=0),
    }, additionalProperties=False)


def resolve_provider_options(options):
    from jsonschema import Draft202012Validator
    result = dict(options)
    Draft202012Validator(provider_schema()).validate(result)
    if any(result.get(key) is not None and not math.isfinite(result[key]) for key in ("wall_timeout", "delay_seconds", "max_delay_seconds")):
        raise ValueError("Provider timing settings must be finite")
    return result


class ProviderError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code.replace("_", " "))


def error_code(error):
    """오류 텍스트는 분류에만 사용한다. 원문은 로그에 넣지 않는다."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, asyncio.CancelledError):
            return "provider_cancelled"
        code = getattr(error, "code", None)
        if isinstance(code, str) and code.startswith(("provider_", "embedding_", "graph_validation_", "usage_")):
            return code
        status = getattr(error, "status_code", None)
        if status in (401, 403):
            return "provider_authentication"
        if status == 429:
            return "provider_rate_limit"
        if status == 408:
            return "provider_timeout"
        if isinstance(status, int) and 500 <= status < 600:
            return "provider_unavailable"
        if isinstance(status, int) and 400 <= status < 500:
            return "provider_invalid_request"
        text, name = str(error).lower(), type(error).__name__.lower()
        if "empty or invalid response" in text or "received: none" in text:
            return "provider_invalid_response"
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in name:
            return "provider_timeout"
        if isinstance(error, ConnectionError) or name in ("apiconnectionerror", "connecterror", "readerror", "remoteprotocolerror"):
            return "provider_connection"
        error = error.__cause__
    return "provider_failed"


TRANSIENT = {"provider_timeout", "provider_connection", "provider_rate_limit", "provider_unavailable",
             "provider_empty_response", "provider_invalid_response"}


async def invoke(operation, request, call, options, *, deadline=None, sdk_defaults=False):
    """각 실제 시도를 관찰한다. 전체 deadline에는 대기·backoff·사용량 관찰도 포함된다."""
    options = resolve_provider_options(options)
    started = time.monotonic()
    end = deadline
    if options.get("wall_timeout") is not None:
        wall_end = started + options["wall_timeout"]
        end = wall_end if end is None else min(end, wall_end)
    attempts = effective_attempts(request, options.get("max_attempts", 1), sdk_defaults=sdk_defaults)
    for attempt in range(attempts):
        remaining = None if end is None else end - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise ProviderError("provider_timeout")
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
            return await asyncio.wait_for(observed_call(operation, copy_params(request), checked), remaining)
        except Exception as error:
            code = error_code(error)
            diagnostic(code, severity="warning", operation=operation, attempt=attempt + 1,
                       model=request.get("model"), elapsed_seconds=time.monotonic() - started)
            if code not in TRANSIENT or attempt + 1 >= attempts:
                if code == "provider_failed" or not code.startswith("provider_"):
                    raise
                raise ProviderError(code) from error
            delay = options.get("delay_seconds", 0) * 2 ** attempt
            if "max_delay_seconds" in options:
                delay = min(options["max_delay_seconds"], delay)
            if end is not None and time.monotonic() + delay >= end:
                raise ProviderError("provider_timeout") from error
            diagnostic("provider_retry", operation=operation, attempt=attempt + 2, delay_seconds=delay)
            await asyncio.sleep(delay)
