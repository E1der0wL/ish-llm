"""LiteLLM의 동기 스트림을 유한 버퍼의 비동기 스트림으로 연결한다. 공급자 호출은 도메인 수명 주기나 저장을 소유하지 않는다.

Adapt synchronous LiteLLM streaming without blocking the application's loop."""

import asyncio
import concurrent.futures
import inspect
import threading
import time
from uuid import uuid4
from contextvars import copy_context
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
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
        from llm.services.infrastructure.observability import record
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
            record("providers", "calls", name="completion")
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
                    record("providers", "cancelled" if stopped.is_set() else "failed" if failure is not None else "completed",
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
                if code == "provider_failed" and completion_fn is not completion:
                    code = None
                raise StreamError(code.replace("_", " ") if code else str(value),
                                  code=code, diagnostic=failure_diagnostic) from value
            yield value
    finally:
        stopped.set()
