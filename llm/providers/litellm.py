"""LiteLLM의 동기 스트림을 유한 버퍼의 비동기 스트림으로 연결한다. 공급자 호출은 도메인 수명 주기나 저장을 소유하지 않는다.

Adapt synchronous LiteLLM streaming without blocking the application's loop."""

from .codes import ProviderErrorCode
import asyncio
import concurrent.futures
import inspect
import threading
import time
import json
from uuid import uuid4
from contextvars import copy_context
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from collections.abc import Mapping
from .streaming import CompletionError, CompletionUpdate
from .calls import current_calls
from llm.errors import CodedError, stable_error_code


class StreamError(CodedError, RuntimeError):
    """A provider/transport failure crossing the synchronous stream bridge."""

    def __init__(self, message, *, code=None, diagnostic=None):
        self.code, self.diagnostic = code, diagnostic
        super().__init__(message)


def completion(**kwargs: Any) -> Iterator[Any]:
    # Import and request creation both happen in the stream's worker thread.
    from .runtime import litellm_sdk
    return litellm_sdk().completion(**kwargs)


def describe_call(operation, request):
    """Normalize LiteLLM request/response semantics at the adapter boundary."""
    from .observations import ModelCall
    from .parameters import copy_params
    from .requests import error_code
    from llm.policies import ExecutionLimitError

    def count_tokens(counter):
        counted = copy_params(request)
        if operation in ("aembedding", "arerank"):
            payload = request.get("input") if operation == "aembedding" else [request.get("query"), request.get("documents")]
            counted["messages"] = [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
            output = 0  # These protocols do not generate completion tokens.
        elif operation in ("completion", "acompletion"):
            output = request.get("max_completion_tokens", request.get("max_tokens"))
            if type(output) is not int or output < 1:
                raise ExecutionLimitError("usage_configuration", "Token quota requires an explicit completion output limit")
        else:
            raise ExecutionLimitError("usage_configuration", "Adapter has no token accounting contract for this operation")
        count = counter(counted)
        if type(count) is not int or count < 0:
            raise ExecutionLimitError("usage_configuration", "Token counter must return a nonnegative integer")
        return count + output

    def read_usage(response):
        usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        if callable(getattr(usage, "model_dump", None)):
            usage = usage.model_dump()
        return usage if isinstance(usage, dict) else {}

    return ModelCall(operation, request.get("model"), count_tokens, read_usage, error_code)


async def resolve_call(operation, request):
    """비스트리밍 SDK 선택과 SDK별 진단. 무거운 import를 이벤트 루프 밖에서 수행한다."""
    from .runtime import litellm_sdk, diagnostic
    sdk = await asyncio.to_thread(litellm_sdk)
    call = getattr(sdk, operation)
    diagnostic("provider_request", operation=operation,
        num_retries=request.get("num_retries"), max_retries=request.get("max_retries"),
        caching=request.get("caching"), cache=request.get("cache"),
        sdk_cache="none" if sdk.cache is None else "configured")
    return call


def prepare_stream(request):
    """Apply this adapter's single-choice completion protocol, before transport."""
    if request.get("stream", True) is not True or request.get("n", 1) != 1:
        raise ValueError("Completion requires stream=True and n=1")
    return {**request, "stream": True}


class LiteLLMCompletionAdapter:
    """LiteLLM-specific request/retry/decoder semantics behind a replaceable boundary."""

    def __init__(self, *, completion_fn=None, completion_decoder=None):
        self.completion_fn = completion if completion_fn is None else completion_fn
        self.completion_decoder = LiteLLMCompletionDecoder if completion_decoder is None else completion_decoder
        if not callable(self.completion_fn) or not callable(self.completion_decoder):
            raise TypeError("Completion function and decoder must be callable")

    def prepare(self, request):
        return prepare_stream(request)

    def describe_call(self, request):
        return describe_call("completion", request)

    def describe_request(self, request):
        from .runtime import request_details
        return request_details(request)

    def effective_attempts(self, request, maximum):
        from .retry import effective_attempts
        return effective_attempts(request, maximum, sdk_defaults=self.completion_fn is completion)

    def classify_error(self, error):
        from .requests import error_code
        return error_code(error)

    def retryable(self, error):
        from .retry import transient
        return transient(error)

    def retry_fields(self, request):
        return ("timeout",)

    def validate_retry(self, request, *, original):
        from .schema import validate_retry_params
        validate_retry_params(request, original=original, operation="acompletion")

    def describe_config(self):
        from .schema import completion_schema
        return completion_schema()

    def validate_processing(self, request, *, original):
        # SDK retry controls belong here, never in a shared processor pipeline.
        for key in ("num_retries", "max_retries", "retry_policy", "client"):
            if (key in request) != (key in original) or request.get(key) != original.get(key):
                raise ValueError(f"Completion processor cannot change {key}")

    def validate_request(self, request, *, require_model=False):
        from .schema import validate_model_params
        validate_model_params(request, require_model=require_model, json_contract=False)
        prepare_stream(request)

    def decoder(self, **limits):
        return self.completion_decoder(**limits)

    def stream(self, request, *, buffer_size):
        return stream_completion(request, completion_fn=self.completion_fn, buffer_size=buffer_size)


async def stream_completion(
    request: dict[str, Any], *, completion_fn: Callable[..., Iterator[Any]] = completion,
    buffer_size: int = 8,
    calls=None,
) -> AsyncIterator[Any]:
    """One bounded bridge per request, with stream ownership in one thread.

    Cancellation stops delivery immediately. A blocked synchronous call cannot
    be forcibly cancelled: the daemon worker closes its stream when the call
    returns or its provider timeout expires. It never executes tools or persists.
    """
    if type(buffer_size) is not int or buffer_size < 1:
        raise ValueError("buffer_size must be a positive integer")
    # SDK timeout은 그대로 전달한다. bridge는 별도 실행 deadline을 만들지 않는다.
    calls = calls if calls is not None else current_calls()
    if calls is not None:
        await calls.acquire()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=buffer_size)
    stopped = threading.Event()
    failure_diagnostic = None

    def send(kind: str, value: Any = None) -> bool:
        if stopped.is_set():
            return False
        pending = queue.put((kind, value))
        try:
            future = asyncio.run_coroutine_threadsafe(pending, loop)
        except RuntimeError:
            pending.close()
            return False
        while not stopped.is_set():
            try:
                future.result(timeout=0.05)
                return True
            except concurrent.futures.TimeoutError:
                continue
            except (concurrent.futures.CancelledError, RuntimeError):
                return False
        future.cancel()
        return False

    def produce() -> None:
        nonlocal failure_diagnostic
        from llm.services.infrastructure.observability import record, ObservationKind, ObservationStatus
        from .requests import error_code
        from .runtime import diagnostic_failure
        started = time.monotonic()
        invoked = False
        stream = None
        failure = None
        stage = "request"
        chunks_received = 0

        def report(error, stage, **details):
            definitions = request.get("tools")
            return diagnostic_failure(error, operation="completion", stage=stage,
                               chunks_received=chunks_received,
                               tool_count=len(definitions) if isinstance(definitions, (list, tuple)) else 0,
                               elapsed_seconds=time.monotonic() - started, **details)
        try:
            if stopped.is_set():
                return
            invoked = True
            record(ObservationKind.PROVIDERS.value, ObservationStatus.CALLS.value, name="completion")
            stream = completion_fn(**request)
            stage = "stream_open"
            iterator = iter(stream)
            stage = "stream_read"
            while not stopped.is_set():
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                chunks_received += 1
                if not send("chunk", chunk):
                    break
        except BaseException as error:
            failure = error
            if not isinstance(error, (asyncio.CancelledError, GeneratorExit)):
                failure_diagnostic = report(error, stage)
        finally:
            # Cleanup is never performed concurrently with next(stream).
            if stream is not None:
                try:
                    close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
                    if close is not None:
                        result = close()
                        if inspect.isawaitable(result):
                            asyncio.run(result)
                except BaseException as error:
                    if not isinstance(error, (asyncio.CancelledError, GeneratorExit)):
                        closing_diagnostic = report(error, "stream_close", secondary=failure is not None)
                        if failure is None:
                            failure_diagnostic = closing_diagnostic
                    if failure is None:
                        failure = error
            try:
                if invoked:
                    record(ObservationKind.PROVIDERS.value, ObservationStatus.CANCELLED.value if stopped.is_set() else ObservationStatus.FAILED.value if failure is not None else ObservationStatus.COMPLETED.value,
                           code=error_code(failure) if failure is not None else None,
                           name="completion", duration_seconds=time.monotonic() - started)
                if not stopped.is_set():
                    send("error" if failure is not None else "end", failure)
            finally:
                if calls is not None:
                    calls.release()

    def run():
        from .runtime import diagnostic_context, request_details
        with diagnostic_context(**request_details(request)) as details:
            details.setdefault("call_id", uuid4().hex)
            details.setdefault("attempt", 1)
            produce()

    try:
        context = copy_context()
        threading.Thread(target=context.run, args=(run,), name="ish-litellm-stream", daemon=True).start()
    except BaseException:
        if calls is not None:
            calls.release()
        raise
    try:
        while True:
            kind, value = await queue.get()
            if kind == "end":
                return
            if kind == "error":
                if isinstance(value, (asyncio.CancelledError, GeneratorExit)):
                    raise value
                from .requests import error_code
                code = stable_error_code(value) or error_code(value)
                # 확장 함수의 미분류 내부 오류는 engine_failed로 남긴다. 알려진 transport 오류만 분류한다.
                if code == ProviderErrorCode.FAILED.value and completion_fn is not completion:
                    code = None
                raise StreamError(code.replace("_", " ") if code else str(value),
                                  code=code, diagnostic=failure_diagnostic) from value
            yield value
    finally:
        stopped.set()


def model_token_count(request: dict) -> int:
    """Local LiteLLM token counting; selection belongs to the explicit caller policy."""
    from .runtime import litellm_sdk
    return litellm_sdk().token_counter(model=request.get("model"), messages=request.get("messages"),
                                     tools=request.get("tools"), tool_choice=request.get("tool_choice"))


def _value(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _usage(value):
    if callable(getattr(value, "model_dump", None)):
        value = value.model_dump()
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if type(item) is int and item >= 0:
            result[key] = item
        elif isinstance(item, Mapping) or callable(getattr(item, "model_dump", None)):
            nested = _usage(item)
            if nested:
                result[key] = nested
    return result


class LiteLLMCompletionDecoder:
    """Interpret one SDK stream; only normalized observations cross into Engines."""

    def __init__(self, *, max_tool_calls=None, max_argument_chars=None, max_output_chars=None):
        self.max_tool_calls = max_tool_calls
        self.max_argument_chars = max_argument_chars
        self.max_output_chars = max_output_chars
        self.content_parts, self.calls = [], {}
        self.finish_reason, self.size = None, 0

    def decode(self, chunk):
        get = _value
        attributes = {key: value for key, source in (("response_id", "id"), ("model", "model"))
                      if isinstance(value := get(chunk, source), str)}
        usage = _usage(get(chunk, "usage"))
        if attributes or usage:
            yield CompletionUpdate(**attributes, usage=usage)
        choices = get(chunk, "choices", [])
        if not isinstance(choices, (list, tuple)):
            raise CompletionError("Expected a completion choices array")
        if not choices:
            return  # Optional usage-only chunk.
        if len(choices) != 1 or get(choices[0], "index", 0) != 0:
            raise CompletionError("Expected one completion choice")
        choice = choices[0]
        delta = get(choice, "delta")
        content = get(delta, "content")
        reasoning = get(delta, "reasoning_content")
        fragments = get(delta, "tool_calls") or []
        if not isinstance(fragments, (list, tuple)):
            raise CompletionError("Expected a tool_calls array")
        if get(delta, "function_call"):
            raise CompletionError("Legacy function_call responses are unsupported")
        if self.finish_reason is not None and (content or fragments or reasoning):
            raise CompletionError("Content after stream termination")
        if reasoning is not None:
            if not isinstance(reasoning, str):
                raise CompletionError("Expected reasoning text delta")
            self.size += len(reasoning)
            if self.max_output_chars is not None and self.size > self.max_output_chars:
                raise CompletionError("Completion output limit exceeded", code="completion_output_limit")
            if reasoning:
                yield CompletionUpdate(reasoning=reasoning)
        if content is not None:
            if not isinstance(content, str):
                raise CompletionError("Expected text delta")
            self.size += len(content)
            if self.max_output_chars is not None and self.size > self.max_output_chars:
                raise CompletionError("Completion output limit exceeded", code="completion_output_limit")
            if content:
                self.content_parts.append(content)
        for fragment in fragments:
            index = get(fragment, "index")
            if type(index) is not int or index < 0:
                raise CompletionError("Invalid tool call index")
            if self.max_tool_calls is not None and index >= self.max_tool_calls:
                raise CompletionError("Too many tool calls", code="completion_tool_limit")
            if get(fragment, "type") not in (None, "function"):
                raise CompletionError("Unsupported tool call type")
            call = self.calls.setdefault(index, {"id": "", "type": "function",
                                                "function": {"name": "", "arguments": ""}})
            function = get(fragment, "function")
            for target, key, value in ((call, "id", get(fragment, "id")),
                                      (call["function"], "name", get(function, "name")),
                                      (call["function"], "arguments", get(function, "arguments"))):
                if value is not None:
                    if not isinstance(value, str):
                        raise CompletionError("Invalid tool call fragment")
                    target[key] += value
            # Opaque SDK IDs may carry thought signatures; never rewrite them.
            if (self.max_argument_chars is not None and
                    (len(call["function"]["arguments"]) > self.max_argument_chars or len(call["id"]) > self.max_argument_chars)):
                raise CompletionError("Tool call size limit exceeded", code="completion_argument_limit")
            if len(call["function"]["name"]) > 64:
                raise CompletionError("Tool call name exceeds protocol limit")
        reason = get(choice, "finish_reason")
        if reason is not None:
            if not isinstance(reason, str):
                raise CompletionError("Invalid finish reason")
            if self.finish_reason is not None and self.finish_reason != reason:
                raise CompletionError("Conflicting stream termination")
            self.finish_reason = reason
            yield CompletionUpdate(finish_reason=reason)
        if content:
            yield CompletionUpdate(text=content)

    def finish(self):
        if self.finish_reason not in ("stop", "tool_calls"):
            raise CompletionError("Completion did not finish normally", code="completion_incomplete")
        if bool(self.calls) != (self.finish_reason == "tool_calls"):
            raise CompletionError("Tool calls do not match finish reason")
        ids = [call["id"] for call in self.calls.values()]
        if (any(not call["id"] or not call["function"]["name"] for call in self.calls.values())
                or len(set(ids)) != len(ids)):
            raise CompletionError("Incomplete or duplicate tool calls")
        result = {"role": "assistant", "content": "".join(self.content_parts) or None}
        if self.calls:
            result["tool_calls"] = [self.calls[index] for index in sorted(self.calls)]
        return result
