"""LiteLLM async streaming and bounded bridges for explicitly injected sync calls.

Provider transport owns its request/stream, never domain lifecycle or persistence.
"""
from llm.providers.diagnostic_values import CompletionReception

from llm.providers.diagnostic_values import ProviderDiagnosticText

from llm.providers.streaming import CompletionErrorCode

from .codes import ProviderErrorCode
from .request_values import SDKOperation, SDKRetryParameters, ModelParameters, LiteLLMParameters
from .response_values import SDKValue, SDKOperationResponse
from .stream_values import StreamBridge, ProviderSDKObservation, ProviderTokenAccounting
from .stream_records import SDKStreamMetadata, SDKStreamChoice, SDKToolFragment, SDKStreamMessage
from .wire_codec import SDKFinishReason
from .retry import sdk_transport_request
import asyncio
import concurrent.futures
import inspect
import threading
import time
import json
from dataclasses import replace
from contextvars import copy_context
from contextlib import aclosing
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from .streaming import CompletionError
from llm.core.model_protocol import CompletionUpdate, ModelRequest, ModelResponse, copy_model
from .wire_codec import decode_message, encode_request, normalize_stop_reason
from .calls import current_calls
from llm.errors import CodedError, stable_error_code


class StreamError(CodedError, RuntimeError):
    """A provider/transport failure crossing the synchronous stream bridge."""

    UNSPECIFIED = None

    def __init__(self, message, *, code=UNSPECIFIED, diagnostic=UNSPECIFIED):
        self.code, self.diagnostic = code, diagnostic
        super().__init__(message)


def completion(**kwargs: Any) -> Iterator[Any]:
    # Import and request creation both happen in the stream's worker thread.
    from .runtime import litellm_sdk

    sdk = litellm_sdk()
    return sdk.completion(**sdk_transport_request(kwargs, sdk))


async def acompletion(**kwargs: Any):
    from .runtime import litellm_sdk

    sdk = await asyncio.to_thread(litellm_sdk)
    return await sdk.acompletion(**sdk_transport_request(kwargs, sdk))


def _uses_sdk(function):
    return function is completion or function is acompletion


def _stream_error(error, function, diagnostic):
    from .requests import error_code

    code = stable_error_code(error) or error_code(error)
    # Unclassified errors in injected functions retain their Engine error boundary.
    if code == ProviderErrorCode.FAILED.value and not _uses_sdk(function):
        code = StreamBridge.UNSPECIFIED
    return StreamError(ProviderDiagnosticText.describe(code) if code else str(error),
                       code=code, diagnostic=diagnostic)


def _counter_request(request):
    """Translate this adapter's accounting input, without a raw public model API."""
    return LiteLLMParameters(request).accounting_request()


def describe_call(operation, request):
    """Normalize LiteLLM request/response semantics at the adapter boundary."""
    from .observations import ModelCall
    from .requests import error_code
    from llm.policies import ExecutionLimitError
    from llm.core.policies import UsageErrorCode

    def count_tokens(counter):
        counted = copy_model(request)
        if operation in ProviderTokenAccounting.AUXILIARY:
            payload = LiteLLMParameters(request).accounting_payload(operation)
            LiteLLMParameters(counted).set_accounting_content(
                json.dumps(payload, ensure_ascii=ProviderTokenAccounting.ENSURE_ASCII)
            )
            output = ProviderTokenAccounting.NO_OUTPUT  # These protocols do not generate completion tokens.
        elif operation in ProviderTokenAccounting.COMPLETION:
            output = LiteLLMParameters(request).output_limit
            if type(output) is not int or output < ProviderTokenAccounting.MINIMUM_OUTPUT:
                raise ExecutionLimitError(
                    UsageErrorCode.CONFIGURATION, ProviderTokenAccounting.OUTPUT_REQUIRED
                )
        else:
            raise ExecutionLimitError(
                UsageErrorCode.CONFIGURATION, ProviderTokenAccounting.UNSUPPORTED_OPERATION
            )
        count = counter(_counter_request(counted))
        if type(count) is not int or count < ProviderTokenAccounting.MINIMUM_INPUT:
            raise ExecutionLimitError(UsageErrorCode.CONFIGURATION, ProviderTokenAccounting.INVALID_COUNT)
        return count + output

    def read_usage(response):
        return SDKOperationResponse(response).observed_usage()

    return ModelCall(operation, ModelParameters(request).optional_model, count_tokens, read_usage, error_code)


async def resolve_call(operation, request):
    """비스트리밍 SDK 선택과 SDK별 진단. 무거운 import를 이벤트 루프 밖에서 수행한다."""
    from .runtime import litellm_sdk

    sdk = await asyncio.to_thread(litellm_sdk)
    sdk_call = getattr(sdk, operation)
    ProviderSDKObservation.request(operation, request, sdk)

    async def call(**parameters):
        return await sdk_call(**sdk_transport_request(parameters, sdk))

    return call


def prepare_stream(request):
    """Apply this adapter's single-choice completion protocol, before transport."""
    return LiteLLMParameters(request).stream_request()


class LiteLLMCompletionAdapter:
    """LiteLLM-specific request/retry/decoder semantics behind a replaceable boundary."""

    # Preparation inspects model values only; transport begins inside stream().
    pure_preparation = True

    INPUT_FIELDS = LiteLLMParameters.INPUT_FIELDS
    PROCESSOR_FIELDS = LiteLLMParameters.PROCESSOR_FIELDS
    AUXILIARY_FIELDS = LiteLLMParameters.AUXILIARY_FIELDS
    AUXILIARY_SDK_RETRIES = SDKRetryParameters.DISABLED
    UNSPECIFIED = None
    SELECTED = True
    UNSELECTED = False
    INVALID_FUNCTIONS = "Completion function and decoder must be callable"
    INVALID_REQUEST = "Completion adapter requires ModelRequest"

    def __init__(self, *, completion_fn=UNSPECIFIED, completion_decoder=UNSPECIFIED):
        self.completion_fn = acompletion if completion_fn is self.UNSPECIFIED else completion_fn
        self.completion_decoder = (
            LiteLLMCompletionDecoder if completion_decoder is self.UNSPECIFIED else completion_decoder
        )
        if not callable(self.completion_fn) or not callable(self.completion_decoder):
            raise TypeError(self.INVALID_FUNCTIONS)

    def prepare(self, request):
        if not isinstance(request, ModelRequest):
            raise TypeError(self.INVALID_REQUEST)
        result = replace(request, parameters=prepare_stream(copy_model(request.parameters)))
        result.validate_exchanges()
        return result

    def initialize(self) -> None:
        """Explicit local SDK preparation; no request, credentials or inference defaults.

        Applications choose when to pay the import cost. An injected transport
        owns its initialization and must not load an unused LiteLLM dependency.
        """
        if _uses_sdk(self.completion_fn):
            from .runtime import prepare_completion

            prepare_completion()

    async def ainitialize(self) -> None:
        """Prepare off-loop, using the SDK runtime's existing import lock/cache."""
        await asyncio.to_thread(self.initialize)

    @classmethod
    def validate_input_ownership(cls, parameters, *, auxiliary=UNSELECTED):
        return LiteLLMParameters(parameters).validate_input_ownership(cls.INPUT_FIELDS, auxiliary=auxiliary)

    @classmethod
    def auxiliary_parameters(
        cls, parameters, *, preserve_retries=UNSELECTED, preserve_output_limits=SELECTED
    ):
        return LiteLLMParameters(parameters).auxiliary_parameters(
            cls.AUXILIARY_FIELDS,
            cls.AUXILIARY_SDK_RETRIES,
            preserve_retries=preserve_retries,
            preserve_output_limits=preserve_output_limits,
        )

    def describe_call(self, request):
        return describe_call(SDKOperation.COMPLETION, encode_request(request))

    def describe_request(self, request):
        from .runtime import request_details

        return request_details(encode_request(request))

    def effective_attempts(self, request, maximum):
        from .retry import effective_attempts

        return effective_attempts(request.parameters, maximum, sdk_defaults=_uses_sdk(self.completion_fn))

    def classify_error(self, error):
        from .requests import error_code

        return error_code(error)

    def retryable(self, error):
        from .retry import transient

        return transient(error)

    def retry_fields(self, request):
        return LiteLLMParameters.RETRY_CHANGEABLE

    def validate_retry(self, request, *, original):
        from .schema import validate_retry_params

        validate_retry_params(
            encode_request(request),
            original=encode_request(original),
            operation=SDKOperation.ASYNC_COMPLETION,
        )

    def describe_config(self):
        from .schema import completion_schema

        return completion_schema()

    def validate_processing(self, request, *, original):
        # SDK retry controls belong here, never in a shared processor pipeline.
        return LiteLLMParameters(request).validate_processing(original, self.PROCESSOR_FIELDS)

    def validate_request(self, request, *, require_model=UNSELECTED):
        from .schema import validate_model_params

        validate_model_params(request, require_model=require_model, json_contract=self.UNSELECTED)
        prepare_stream(request)

    def decoder(self, **limits):
        return self.completion_decoder(**limits)

    def stream(self, request, *, buffer_size):
        return stream_completion(
            encode_request(request), completion_fn=self.completion_fn, buffer_size=buffer_size
        )


async def _stream_async(request, *, buffer_size, calls):
    from llm.services.infrastructure.observability import record, ObservationKind, ObservationStatus
    from llm.services.infrastructure.storage import drain_on_cancel
    from .runtime import diagnostic_context, request_details, diagnostic_failure
    from .requests import error_code

    calls = calls if calls is not StreamBridge.UNSPECIFIED else current_calls()
    if calls is not StreamBridge.UNSPECIFIED:
        await calls.acquire()
    queue = asyncio.Queue(maxsize=buffer_size)
    stopped = False
    closing = False

    async def produce():
        nonlocal closing
        # Creation, iteration and closing share one Task: SDK context tokens and
        # HTTP streams must not migrate to a separate cleanup Task.
        with diagnostic_context(**request_details(request)) as details:
            StreamBridge.observe_attempt(details)
            started = time.monotonic()
            stream = failure = failure_diagnostic = StreamBridge.UNSPECIFIED
            stage, chunks = StreamBridge.REQUEST, StreamBridge.INITIAL_CHUNKS

            def report(error, stage, **extra):
                return diagnostic_failure(error, operation=SDKOperation.COMPLETION, stage=stage,
                    **CompletionReception.stream_details(chunks),
                    tool_count=ProviderSDKObservation.tool_count(request),
                    elapsed_seconds=time.monotonic() - started, **extra)

            record(ObservationKind.PROVIDERS.value, ObservationStatus.CALLS.value,
                   name=SDKOperation.COMPLETION)
            try:
                stream = await acompletion(**request)
                stage = StreamBridge.OPEN
                iterator = aiter(stream)
                stage = StreamBridge.READ
                async for chunk in iterator:
                    chunks += StreamBridge.CHUNK_INCREMENT
                    await queue.put((StreamBridge.CHUNK, chunk))
            except BaseException as error:
                failure = error
                if not isinstance(error, (asyncio.CancelledError, GeneratorExit)):
                    failure_diagnostic = report(error, stage)
            finally:
                closing = True
                if stream is not StreamBridge.UNSPECIFIED:
                    try:
                        close = StreamBridge.closing_method(stream, asynchronous=True)
                        if close is not StreamBridge.UNSPECIFIED:
                            result = close()
                            if inspect.isawaitable(result):
                                await result
                    except BaseException as error:
                        if not isinstance(error, (asyncio.CancelledError, GeneratorExit)):
                            diagnostic = report(error, StreamBridge.CLOSE,
                                                secondary=failure is not StreamBridge.UNSPECIFIED)
                            if failure is StreamBridge.UNSPECIFIED:
                                failure_diagnostic = diagnostic
                        if failure is StreamBridge.UNSPECIFIED:
                            failure = error
                cancelled = stopped or isinstance(failure, (asyncio.CancelledError, GeneratorExit))
                record(ObservationKind.PROVIDERS.value,
                    (ObservationStatus.CANCELLED.value if cancelled else
                     ObservationStatus.FAILED.value if failure is not StreamBridge.UNSPECIFIED else
                     ObservationStatus.COMPLETED.value),
                    code=error_code(failure) if failure is not StreamBridge.UNSPECIFIED else None,
                    name=SDKOperation.COMPLETION, duration_seconds=time.monotonic() - started)
                closing = False
            if not stopped:
                value = failure
                if failure is not None and not isinstance(failure, (asyncio.CancelledError, GeneratorExit)):
                    value = _stream_error(failure, acompletion, failure_diagnostic)
                    value.__cause__ = failure
                await queue.put((StreamBridge.ERROR if failure is not None else StreamBridge.END, value))

    producer = None
    try:
        producer = asyncio.create_task(produce())
        while True:
            kind, value = await queue.get()
            if kind == StreamBridge.END:
                return
            if kind == StreamBridge.ERROR:
                raise value
            yield value
    finally:
        stopped = True
        try:
            if producer is not None:
                # One cancellation interrupts I/O. Repeated caller cancellation
                # cannot interrupt the producer's same-Task stream cleanup.
                if not producer.done() and not closing:
                    producer.cancel()
                try:
                    await drain_on_cancel(producer)
                except asyncio.CancelledError:
                    if not producer.cancelled():
                        raise
        finally:
            if calls is not StreamBridge.UNSPECIFIED:
                calls.release()


async def stream_completion(
    request: dict[str, Any],
    *,
    completion_fn: Callable = acompletion,
    buffer_size: int = StreamBridge.BUFFER_SIZE,
    calls=StreamBridge.UNSPECIFIED,
) -> AsyncIterator[Any]:
    """Native async transport, or one bounded worker for an injected sync call.

    Cancellation stops delivery immediately. A blocked synchronous call cannot
    be forcibly cancelled: the daemon worker closes its stream when the call
    returns or its provider timeout expires. It never executes tools or persists.
    """
    if type(buffer_size) is not int or buffer_size < StreamBridge.MINIMUM_BUFFER_SIZE:
        raise ValueError(StreamBridge.INVALID_BUFFER)
    if completion_fn is acompletion:
        async with aclosing(_stream_async(request, buffer_size=buffer_size, calls=calls)) as stream:
            async for chunk in stream:
                yield chunk
        return
    # SDK timeout은 그대로 전달한다. bridge는 별도 실행 deadline을 만들지 않는다.
    calls = calls if calls is not StreamBridge.UNSPECIFIED else current_calls()
    if calls is not StreamBridge.UNSPECIFIED:
        await calls.acquire()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=buffer_size)
    stopped = threading.Event()
    failure_diagnostic = StreamBridge.UNSPECIFIED

    def send(kind: str, value: Any = StreamBridge.UNSPECIFIED) -> bool:
        if stopped.is_set():
            return StreamBridge.INACTIVE
        pending = queue.put((kind, value))
        try:
            future = asyncio.run_coroutine_threadsafe(pending, loop)
        except RuntimeError:
            pending.close()
            return StreamBridge.INACTIVE
        while not stopped.is_set():
            try:
                future.result(timeout=StreamBridge.DELIVERY_POLL_SECONDS)
                return StreamBridge.ACTIVE
            except concurrent.futures.TimeoutError:
                continue
            except (concurrent.futures.CancelledError, RuntimeError):
                return StreamBridge.INACTIVE
        future.cancel()
        return StreamBridge.INACTIVE

    def produce() -> None:
        nonlocal failure_diagnostic
        from llm.services.infrastructure.observability import record, ObservationKind, ObservationStatus
        from .requests import error_code
        from .runtime import diagnostic_failure

        started = time.monotonic()
        invoked = StreamBridge.INACTIVE
        stream = StreamBridge.UNSPECIFIED
        failure = StreamBridge.UNSPECIFIED
        stage = StreamBridge.REQUEST
        chunks_received = StreamBridge.INITIAL_CHUNKS

        def report(error, stage, **details):
            return diagnostic_failure(
                error,
                operation=SDKOperation.COMPLETION,
                stage=stage,
                **CompletionReception.stream_details(chunks_received),
                tool_count=ProviderSDKObservation.tool_count(request),
                elapsed_seconds=time.monotonic() - started,
                **details,
            )

        try:
            if stopped.is_set():
                return
            invoked = StreamBridge.ACTIVE
            record(
                ObservationKind.PROVIDERS.value, ObservationStatus.CALLS.value, name=SDKOperation.COMPLETION
            )
            stream = completion_fn(**request)
            stage = StreamBridge.OPEN
            iterator = iter(stream)
            stage = StreamBridge.READ
            while not stopped.is_set():
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                chunks_received += StreamBridge.CHUNK_INCREMENT
                if not send(StreamBridge.CHUNK, chunk):
                    break
        except BaseException as error:
            failure = error
            if not isinstance(error, (asyncio.CancelledError, GeneratorExit)):
                failure_diagnostic = report(error, stage)
        finally:
            # Cleanup is never performed concurrently with next(stream).
            if stream is not StreamBridge.UNSPECIFIED:
                try:
                    close = StreamBridge.closing_method(stream)
                    if close is not StreamBridge.UNSPECIFIED:
                        result = close()
                        if inspect.isawaitable(result):
                            asyncio.run(result)
                except BaseException as error:
                    if not isinstance(error, (asyncio.CancelledError, GeneratorExit)):
                        closing_diagnostic = report(
                            error, StreamBridge.CLOSE, secondary=failure is not StreamBridge.UNSPECIFIED
                        )
                        if failure is StreamBridge.UNSPECIFIED:
                            failure_diagnostic = closing_diagnostic
                    if failure is StreamBridge.UNSPECIFIED:
                        failure = error
            try:
                if invoked:
                    record(
                        ObservationKind.PROVIDERS.value,
                        (
                            ObservationStatus.CANCELLED.value
                            if stopped.is_set()
                            else (
                                ObservationStatus.FAILED.value
                                if failure is not StreamBridge.UNSPECIFIED
                                else ObservationStatus.COMPLETED.value
                            )
                        ),
                        code=(
                            error_code(failure)
                            if failure is not StreamBridge.UNSPECIFIED
                            else StreamBridge.UNSPECIFIED
                        ),
                        name=SDKOperation.COMPLETION,
                        duration_seconds=time.monotonic() - started,
                    )
                if not stopped.is_set():
                    send(
                        StreamBridge.ERROR if failure is not StreamBridge.UNSPECIFIED else StreamBridge.END,
                        failure,
                    )
            finally:
                if calls is not StreamBridge.UNSPECIFIED:
                    calls.release()

    def run():
        from .runtime import diagnostic_context, request_details

        with diagnostic_context(**request_details(request)) as details:
            StreamBridge.observe_attempt(details)
            produce()

    try:
        context = copy_context()
        threading.Thread(
            target=context.run, args=(run,), name=StreamBridge.THREAD_NAME, daemon=StreamBridge.DAEMON
        ).start()
    except BaseException:
        if calls is not StreamBridge.UNSPECIFIED:
            calls.release()
        raise
    try:
        while True:
            kind, value = await queue.get()
            if kind == StreamBridge.END:
                return
            if kind == StreamBridge.ERROR:
                if isinstance(value, (asyncio.CancelledError, GeneratorExit)):
                    raise value
                raise _stream_error(value, completion_fn, failure_diagnostic) from value
            yield value
    finally:
        stopped.set()


def model_token_count(request: ModelRequest) -> int:
    """Local LiteLLM token counting; selection belongs to the explicit caller policy."""
    from .runtime import litellm_sdk

    request = encode_request(request)
    return LiteLLMParameters(request).count_tokens(litellm_sdk())


def _value(value, key, default=SDKValue.UNSPECIFIED):
    return SDKValue.field(value, key, default)


def _usage(value):
    return SDKValue.usage(value)


def _merge_opaque(previous, fragment):
    """Preserve SDK metadata fragments without interpreting them outside Provider."""
    return SDKValue.merge_opaque(previous, fragment)


def _sdk_mapping(value):
    return SDKValue.mapping(value)


class LiteLLMCompletionDecoder:
    """Interpret one SDK stream; only normalized observations cross into Engines."""

    UNSPECIFIED = None
    INITIAL_SIZE = 0
    FIRST_CALL_INDEX = SDKStreamChoice.FIRST_INDEX
    NORMAL_FINISH = (SDKFinishReason.STOP, SDKFinishReason.TOOL_CALLS)
    INVALID_TOOL_CALLS = "Expected a tool_calls array"
    LEGACY_FUNCTION_CALL = "Legacy function_call responses are unsupported"
    AFTER_TERMINATION = "Content after stream termination"
    INVALID_REASONING = "Expected reasoning text delta"
    OUTPUT_LIMIT = "Completion output limit exceeded"
    INVALID_TEXT = "Expected text delta"
    INVALID_CALL_INDEX = "Invalid tool call index"
    TOOL_LIMIT = "Too many tool calls"
    INVALID_FINISH = "Invalid finish reason"
    CONFLICTING_FINISH = "Conflicting stream termination"
    INCOMPLETE = "Completion did not finish normally"
    MISMATCHED_FINISH = "Tool calls do not match finish reason"
    INCOMPLETE_CALLS = "Incomplete or duplicate tool calls"
    INVALID_RESPONSE = "Invalid completed model response"

    def __init__(
        self, *, max_tool_calls=UNSPECIFIED, max_argument_chars=UNSPECIFIED, max_output_chars=UNSPECIFIED
    ):
        self.max_tool_calls = max_tool_calls
        self.max_argument_chars = max_argument_chars
        self.max_output_chars = max_output_chars
        self.content_parts, self.calls = [], {}
        self.finish_reason, self.size = self.UNSPECIFIED, self.INITIAL_SIZE
        self.opaque, self.usage, self.response_id, self.model = (
            {},
            self.UNSPECIFIED,
            self.UNSPECIFIED,
            self.UNSPECIFIED,
        )

    def decode(self, chunk):
        metadata = SDKStreamMetadata(chunk)
        usage = metadata.usage
        self.response_id = metadata.response_id(self.response_id)
        self.model = metadata.model(self.model)
        if usage:
            self.usage = {**(self.usage or {}), **usage}
        if metadata.present:
            yield metadata.update()
        choice = SDKStreamChoice.read(chunk)
        if choice is self.UNSPECIFIED:
            return  # Optional usage-only chunk.
        delta = choice.delta
        delta.merge_opaque(self.opaque)
        content = delta.content
        reasoning = delta.reasoning
        fragments = delta.tool_calls
        fragments = [] if fragments is self.UNSPECIFIED else fragments
        if not isinstance(fragments, (list, tuple)):
            raise CompletionError(self.INVALID_TOOL_CALLS)
        if delta.legacy_function_call:
            raise CompletionError(self.LEGACY_FUNCTION_CALL)
        if self.finish_reason is not self.UNSPECIFIED and (content or fragments or reasoning):
            raise CompletionError(self.AFTER_TERMINATION)
        if reasoning is not self.UNSPECIFIED:
            if not isinstance(reasoning, str):
                raise CompletionError(self.INVALID_REASONING)
            self.size += len(reasoning)
            if self.max_output_chars is not self.UNSPECIFIED and self.size > self.max_output_chars:
                raise CompletionError(self.OUTPUT_LIMIT, code=CompletionErrorCode.OUTPUT_LIMIT)
            if reasoning:
                yield CompletionUpdate(reasoning=reasoning)
        if content is not self.UNSPECIFIED:
            if not isinstance(content, str):
                raise CompletionError(self.INVALID_TEXT)
            self.size += len(content)
            if self.max_output_chars is not self.UNSPECIFIED and self.size > self.max_output_chars:
                raise CompletionError(self.OUTPUT_LIMIT, code=CompletionErrorCode.OUTPUT_LIMIT)
            if content:
                self.content_parts.append(content)
        for fragment in fragments:
            index = SDKToolFragment.index(fragment)
            if type(index) is not int or index < self.FIRST_CALL_INDEX:
                raise CompletionError(self.INVALID_CALL_INDEX)
            if self.max_tool_calls is not self.UNSPECIFIED and index >= self.max_tool_calls:
                raise CompletionError(self.TOOL_LIMIT, code=CompletionErrorCode.TOOL_LIMIT)
            SDKToolFragment.validate_type(fragment)
            call = self.calls.setdefault(index, SDKToolFragment())
            call.append(fragment)
            # Opaque SDK IDs may carry thought signatures; never rewrite them.
            call.validate_size(self.max_argument_chars)
        reason = choice.finish_reason
        if reason is not self.UNSPECIFIED:
            if not isinstance(reason, str):
                raise CompletionError(self.INVALID_FINISH)
            if self.finish_reason is not self.UNSPECIFIED and self.finish_reason != reason:
                raise CompletionError(self.CONFLICTING_FINISH)
            self.finish_reason = reason
            yield CompletionUpdate(finish_reason=reason, termination=normalize_stop_reason(reason))
        if content:
            yield CompletionUpdate(text=content)

    def finish(self):
        if self.finish_reason not in self.NORMAL_FINISH:
            raise CompletionError(self.INCOMPLETE, code=CompletionErrorCode.INCOMPLETE)
        if bool(self.calls) != (self.finish_reason == SDKFinishReason.TOOL_CALLS):
            raise CompletionError(self.MISMATCHED_FINISH)
        ids = [call.id for call in self.calls.values()]
        if any(call.incomplete for call in self.calls.values()) or len(set(ids)) != len(ids):
            raise CompletionError(self.INCOMPLETE_CALLS)
        result = SDKStreamMessage.create(self.opaque, self.content_parts, self.calls)
        try:
            message = decode_message(result)
        except (TypeError, ValueError) as error:
            raise CompletionError(self.INVALID_RESPONSE) from error
        return ModelResponse(
            message,
            normalize_stop_reason(self.finish_reason),
            self.usage,
            self.response_id,
            self.model,
            self.finish_reason,
        )
