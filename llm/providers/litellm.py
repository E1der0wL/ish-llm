"""LiteLLM의 동기 스트림을 유한 버퍼의 비동기 스트림으로 연결한다. 공급자 호출은 도메인 수명 주기나 저장을 소유하지 않는다.

Adapt synchronous LiteLLM streaming without blocking the application's loop."""

import asyncio
import concurrent.futures
import inspect
import threading
from contextvars import copy_context
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from .calls import current_calls


class StreamError(RuntimeError):
    """A provider/transport failure crossing the synchronous stream bridge."""


def completion(**kwargs: Any) -> Iterator[Any]:
    # Import and request creation both happen in the stream's worker thread.
    from .runtime import litellm_sdk
    return litellm_sdk().completion(**kwargs)


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
        stream = None
        failure = None
        try:
            if stopped.is_set():
                return
            stream = completion_fn(**request)
            iterator = iter(stream)
            while not stopped.is_set():
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                if not send("chunk", chunk):
                    break
        except BaseException as error:
            failure = error
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
                    if failure is None:
                        failure = error
            try:
                if not stopped.is_set():
                    send("error" if failure is not None else "end", failure)
            finally:
                if calls is not None:
                    calls.release()

    try:
        context = copy_context()
        threading.Thread(target=context.run, args=(produce,), name="ish-litellm-stream", daemon=True).start()
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
                if completion_fn is completion:
                    from .requests import error_code
                    error = StreamError(error_code(value))
                    error.code = error_code(value)
                    raise error from value
                raise StreamError(str(value)) from value
            yield value
    finally:
        stopped.set()
