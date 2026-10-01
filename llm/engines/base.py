"""Engine 이벤트 계약과 BaseEngine의 공통 Step/스트리밍 처리를 정의한다. Engine은 이벤트를 생성하며 영속 파일에는 직접 쓰지 않는다.

Engine contract, Step event lifecycle, and reusable LiteLLM streaming."""

import asyncio
import inspect
import json
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import field, replace
from typing import Any, Optional, Protocol, TypeVar, Union

from llm.compat import StrEnum, aclosing, dataclass, timeout
from llm.core.models import Message, Project, Run, RunStatus, Session, new_id, now
from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef
from llm.errors import stable_error_code
from llm.core.results import CompletionResult, EngineOutput, EngineDelta
from llm.core.interactions import InteractionRequest
from llm.components.tools import ToolRegistry
from llm.providers.litellm import completion, stream_completion
from llm.providers.parameters import copy_params
from llm.services.runtime.usage import current_usage


_OutputValue = TypeVar("_OutputValue", EngineDelta, EngineOutput)


class EngineEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    OUTPUT = "output"
    COMPLETION = "completion"
    STEP_STARTED = "step_started"
    STEP_UPDATED = "step_updated"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    STEP_INTERRUPTED = "step_interrupted"
    STEP_CANCELLED = "step_cancelled"
    CHECKPOINT = "checkpoint"
    PAUSED = "paused"
    INTERACTION_CHANGED = "interaction_changed"


@dataclass(frozen=True, slots=True)
# Engine에서 서비스로 전달하는 관찰 이벤트. 사용자 이벤트는 문자열 이름을 사용한다.
class EngineEvent:
    type: Union[EngineEventType, str]
    delta: Optional[EngineDelta] = None
    step_id: Optional[str] = None
    kind: str = "llm"
    name: str = ""
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None
    completion: Optional[CompletionResult] = None
    output: Optional[EngineOutput] = None
    interaction: Optional[InteractionRequest] = None
    diagnostic: Optional[Diagnostic] = None
    progress: Optional[OperationProgress] = None
    # 실행 중 fail-fast 합성용. Diagnostic의 임의 관찰 code와 달리 명시적 CodedError만 전달한다.
    # StepEventRecorder는 이 런타임 필드를 저장하지 않는다.
    failure_code: Optional[str] = None

    def __post_init__(self):
        # Step 상태 표시를 공통 계약으로 제공한다. 실제 상태 전이는 서비스가 수행한다.
        if self.diagnostic is not None and not isinstance(self.diagnostic, Diagnostic):
            raise TypeError("diagnostic must be Diagnostic")
        if self.progress is not None and not isinstance(self.progress, OperationProgress):
            raise TypeError("progress must be OperationProgress")
        if self.failure_code is not None and (self.type != EngineEventType.STEP_FAILED
                or not isinstance(self.failure_code, str) or not self.failure_code.strip()):
            raise ValueError("failure_code requires a nonempty code on STEP_FAILED")
        phases = {"step_started": "started", "step_updated": "running", "step_completed": "completed",
                  "step_failed": "failed", "step_interrupted": "interrupted", "step_cancelled": "cancelled"}
        if self.step_id is not None and self.type in phases:
            source = ResourceRef("step", self.step_id, step_id=self.step_id)
            if self.progress is None:
                phase = self.metadata.get("phase")
                object.__setattr__(self, "progress", OperationProgress(source,
                    phase if isinstance(phase, str) and phase.strip() else phases[self.type]))
            if self.type == EngineEventType.STEP_FAILED and self.diagnostic is None:
                code = self.metadata.get("error_code")
                object.__setattr__(self, "diagnostic", Diagnostic(
                    code if isinstance(code, str) and code.strip() else "step_failed",
                    self.error or "Step failed", source=source))


@dataclass(frozen=True, slots=True)
# Run별 읽기 스냅샷과 임시 state/capability를 전달한다.
class EngineContext:
    project: Project
    session: Session
    run: Run
    messages: tuple[Message, ...]
    # Per-Run snapshot of Project capabilities, never part of persisted models.
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    # Preparation outputs and handles for this Run only; never persisted.
    state: dict[str, Any] = field(default_factory=dict)
    # Engine이 선언한 capability만 Run별로 구성한다. 런타임 값은 저장하지 않는다.
    capabilities: dict[str, Any] = field(default_factory=dict)
    # 명시적 재개에서만 제공하는 저장 스냅샷. 런타임 핸들은 포함하지 않는다.
    checkpoint: Optional[dict] = None
    # 부모가 이벤트 저장을 중계하는 자식 실행 범위. Step 출력 소유권과 구분한다.
    checkpoint_scope: Optional[str] = None
    # 백엔드가 주입하는 실행 정책. Run 전체(중첩 Agent/Graph 포함)가 공유한다.
    tool_scope: Any = None
    pending_work: Any = None
    completion_policy: Any = None
    # 중첩 엔진의 최종 결과는 이 Step에 귀속한다. None은 최상위 Run이다.
    output_step_id: Optional[str] = None
    output_visibility: str = "user"

    def settings(self, engine: Optional[str] = None) -> dict:
        return self.project.config.for_engine(self.run.engine if engine is None else engine, self.session.config)


class Engine(Protocol):
    def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]: ...


def required_capabilities(engine) -> tuple[str, ...]:
    """선언하지 않은 Engine은 외부 기능 없이 실행한다."""
    names = getattr(engine, "required_capabilities", ())
    if (not isinstance(names, tuple) or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Engine required_capabilities must be a tuple of distinct names")
    return names


# 작업 함수를 공통 Step 이벤트와 스트리밍 출력으로 감싼다.
class BaseEngine:
    """Adapt a developer operation to the existing Engine event contract.

    run(context) 또는 action에서 문자열/EngineDelta와 마지막 EngineOutput을 yield한다.
    async 함수는 EngineOutput 또는 None을 반환한다. execute()가 Step과 출력의
    소유권/이벤트를 연결한다. 복잡한 실행기는 execute()에서 아래 출력 메서드를
    재사용한다. 실행별 상태는 인스턴스에 저장하지 않는다.
    Labels/metadata/error_message are trusted, persistable developer constants.
    """

    required_capabilities = ()

    def __init__(self, name: str = "Step", *, kind: str = "custom",
                 action: Optional[Callable[[EngineContext],
                     Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]]] = None,
                 timeout_seconds: Optional[float] = None,
                 metadata: Optional[dict] = None,
                 error_message: str = "Step execution failed",
                 completion_fn: Callable[..., Iterator[Any]] = completion,
                 buffer_size: int = 8, max_tool_calls: Optional[int] = None,
                 max_argument_chars: Optional[int] = None,
                 max_output_chars: Optional[int] = None) -> None:
        if not isinstance(name, str) or not name.strip() or not isinstance(kind, str) or not kind.strip():
            raise ValueError("Step requires a name and kind")
        if action is not None and not callable(action):
            raise TypeError("Step action must be callable")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("Step timeout must be positive and finite, or None")
        if not isinstance(error_message, str) or not error_message.strip():
            raise ValueError("Step error message must be a nonempty string")
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("Step metadata must be a dictionary")
        json.dumps(metadata or {}, allow_nan=False)
        self.name, self.kind = name, kind
        self.action = action
        self.timeout_seconds = timeout_seconds
        self.metadata = deepcopy(metadata or {})
        self.error_message = error_message
        for value in (buffer_size, max_tool_calls, max_argument_chars, max_output_chars):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("Completion limits must be positive integers")
        if not callable(completion_fn):
            raise TypeError("completion_fn must be callable")
        self.completion_fn = completion_fn
        self.buffer_size = buffer_size
        if type(buffer_size) is not int or buffer_size < 1:
            raise ValueError("buffer_size must be a positive integer")
        self.max_tool_calls = max_tool_calls
        self.max_argument_chars = max_argument_chars
        self.max_output_chars = max_output_chars

    def step(self, context: EngineContext,
             action: Callable[[EngineContext], Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]], *,
             name: str, kind: str = "custom", timeout_seconds: Optional[float] = None,
             metadata: Optional[dict] = None,
             error_message: str = "Step execution failed") -> AsyncIterator[EngineEvent]:
        """Wrap one operation when an Engine needs several Steps in execute().

        Consume under aclosing() so early exit closes the operation promptly.
        Only events are emitted; RunManager and StepManager own persistence.
        """
        return BaseEngine(name, kind=kind, action=action, timeout_seconds=timeout_seconds,
                          metadata=metadata, error_message=error_message)._execute(context, publish_output=False)

    @staticmethod
    def _bind_output(context: EngineContext, value: _OutputValue, *,
                     step_id: Optional[str] = None) -> _OutputValue:
        """호출별 소유권을 적용한다. 내부 출력은 외부 표시 범위로 승격하지 않는다."""
        if not isinstance(value, (EngineDelta, EngineOutput)):
            raise TypeError("Expected EngineDelta or EngineOutput")
        if context.output_visibility not in ("user", "internal"):
            raise ValueError("Invalid context output visibility")
        owner = context.output_step_id if step_id is None else step_id
        visibility = "internal" if "internal" in (context.output_visibility, value.visibility) else "user"
        return replace(value, output_id=owner or context.run.id, step_id=owner,
                       visibility=visibility, sequence=0)

    @staticmethod
    def delta_event(context: EngineContext, value: Union[str, EngineDelta], *,
                    step_id: Optional[str] = None) -> EngineEvent:
        """문자열/델타를 현재 Run 또는 명시한 Step의 변경 이벤트로 만든다."""
        if isinstance(value, str):
            value = EngineDelta(context.run.id, text=value)
        if not isinstance(value, EngineDelta):
            raise TypeError("delta_event requires text or EngineDelta")
        delta = BaseEngine._bind_output(context, value, step_id=step_id)
        return EngineEvent(EngineEventType.TEXT_DELTA, step_id=delta.step_id, delta=delta)

    @staticmethod
    def output_event(context: EngineContext, output: EngineOutput) -> EngineEvent:
        """최상위 결과는 Run, 중첩 결과는 context.output_step_id에 연결한다."""
        if not isinstance(output, EngineOutput) or not output.final:
            raise ValueError("output_event requires a final EngineOutput")
        output = BaseEngine._bind_output(context, output)
        return EngineEvent(EngineEventType.OUTPUT, step_id=output.step_id, output=output)

    @staticmethod
    def step_failed_event(step_id: str, error: Exception, *, message: Optional[str] = None,
                          diagnostic: Optional[Diagnostic] = None, metadata: Optional[dict] = None,
                          code: str = "step_failed") -> EngineEvent:
        """실패 의미를 전달한다. 명시 진단/기존 진단은 유지하고 누락 source만 Step에 연결한다."""
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError("A failed Step requires an ID")
        if diagnostic is None:
            diagnostic = getattr(error, "diagnostic", None)
        source = ResourceRef("step", step_id, step_id=step_id)
        if diagnostic is None:
            diagnostic = Diagnostic.from_exception(error, code=code, source=source)
        elif isinstance(diagnostic, Diagnostic) and diagnostic.source is None:
            diagnostic = replace(diagnostic, source=source)
        return EngineEvent(EngineEventType.STEP_FAILED, step_id=step_id,
            error=str(error) if message is None else message, diagnostic=deepcopy(diagnostic),
            metadata=deepcopy(metadata or {}), failure_code=stable_error_code(error))

    @staticmethod
    def step_completed_event(context: EngineContext, step_id: str, output: EngineOutput, *,
                             metadata: Optional[dict] = None) -> EngineEvent:
        """Step 결과와 완료를 함께 전달한다. Step 시작/실행 순서는 호출자가 정한다."""
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError("A completed Step requires an ID")
        if not isinstance(output, EngineOutput) or not output.final:
            raise ValueError("Step completion requires a final EngineOutput")
        output = BaseEngine._bind_output(context, output, step_id=step_id)
        return EngineEvent(EngineEventType.STEP_COMPLETED, step_id=step_id, output=output,
                           metadata=deepcopy(metadata or {}))

    @staticmethod
    def copy_params(value: Any) -> Any:
        """Copy builtin option containers, keeping live SDK clients/callbacks intact."""
        return copy_params(value)

    @staticmethod
    def chunk_value(value: Any, key: str, default: Any = None) -> Any:
        """Read either LiteLLM SDK attributes or equivalent dictionary chunks."""
        return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)

    async def stream_completion(self, request: Mapping[str, Any], *,
                                response: Optional[dict] = None,
                                include_events: bool = True) -> AsyncIterator[Union[str, EngineEvent]]:
        """Yield text and completion observations; inherited execute() routes both.

        include_events=False is for standalone text consumers without persistence.
        response receives the assistant message on success only. Observations
        contain no prompts, tool arguments, headers, or arbitrary SDK payloads.
        """
        from llm.providers.retry import retry_settings, transient, effective_attempts
        policy = retry_settings()
        maximum = effective_attempts(request, policy.get("max_retries", 0) + 1,
                                     sdk_defaults=self.completion_fn is completion) - 1
        for attempt in range(maximum + 1):
            progress = {"received": False}
            result = CompletionResult(model=request.get("model") if isinstance(request.get("model"), str) else None)
            usage_scope = current_usage()
            if usage_scope is not None:
                result.reserved_tokens = await usage_scope.reservation(self.copy_params(dict(request)))
            started = time.monotonic()
            if include_events:
                yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
            try:
                async with aclosing(self._stream_completion(request, response, result, progress)) as stream:
                    async for item in stream:
                        if include_events or isinstance(item, str):
                            yield item
            except (asyncio.CancelledError, GeneratorExit):
                # RunManager finalizes the last durable observation; never yield here.
                raise
            except Exception as error:
                result.status = RunStatus.FAILED
                result.ended_at = now()
                result.duration_seconds = time.monotonic() - started
                if include_events:
                    yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
                if progress["received"] or attempt >= maximum or not transient(error):
                    raise
                delay = policy.get("delay_seconds", 0) * (2 ** attempt)
                if "max_delay_seconds" in policy:
                    delay = min(delay, policy["max_delay_seconds"])
                await asyncio.sleep(delay)
                continue
            else:
                result.status = RunStatus.COMPLETED
                result.ended_at = now()
                result.duration_seconds = time.monotonic() - started
                if include_events:
                    yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))

                return

    @staticmethod
    def _usage(value: Any) -> dict:
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
                nested = BaseEngine._usage(item)
                if nested:
                    result[key] = nested
        return result

    async def _stream_completion(self, request: Mapping[str, Any], response: Optional[dict],
                                 result: CompletionResult, progress: dict) -> AsyncIterator[Union[str, EngineEvent]]:
        params = self.copy_params(dict(request))
        if params.get("stream", True) is not True or params.get("n", 1) != 1:
            raise ValueError("Completion requires stream=True and n=1")
        params["stream"] = True
        if response is not None and not isinstance(response, dict):
            raise TypeError("Completion response must be a dictionary")
        content_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish_reason = None
        size = 0
        get = self.chunk_value
        async with aclosing(stream_completion(
            params, completion_fn=self.completion_fn, buffer_size=self.buffer_size,
        )) as chunks:
            async for chunk in chunks:
                # 텍스트 없이 Tool 인자 일부만 온 경우도 이미 응답을 받은 호출이다.
                progress["received"] = True
                changed = False
                for attribute, key in (("response_id", "id"), ("model", "model")):
                    value = get(chunk, key)
                    if isinstance(value, str) and value != getattr(result, attribute):
                        setattr(result, attribute, value)
                        changed = True
                usage = self._usage(get(chunk, "usage"))
                if usage:
                    updated = {**result.usage, **usage}
                    if updated != result.usage:
                        result.usage = updated
                        changed = True
                if changed:
                    yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
                choices = get(chunk, "choices", [])
                if not choices:
                    continue  # Optional usage-only chunk; no text to deliver.
                if len(choices) != 1 or get(choices[0], "index", 0) != 0:
                    raise ValueError("Expected one completion choice")
                choice = choices[0]
                delta = get(choice, "delta")
                content = get(delta, "content")
                fragments = get(delta, "tool_calls") or []
                if get(delta, "function_call"):
                    raise ValueError("Legacy function_call responses are unsupported")
                if finish_reason is not None and (content or fragments):
                    raise ValueError("Content after stream termination")
                if content is not None:
                    if not isinstance(content, str):
                        raise ValueError("Expected text delta")
                    size += len(content)
                    if self.max_output_chars is not None and size > self.max_output_chars:
                        raise ValueError("Completion output limit exceeded")
                    if content:
                        content_parts.append(content)
                for fragment in fragments:
                    index = get(fragment, "index")
                    if type(index) is not int or index < 0 or (self.max_tool_calls is not None and index >= self.max_tool_calls):
                        raise ValueError("Invalid tool call index or too many calls")
                    if get(fragment, "type") not in (None, "function"):
                        raise ValueError("Unsupported tool call type")
                    call = calls.setdefault(index, {"id": "", "type": "function",
                                                   "function": {"name": "", "arguments": ""}})
                    function = get(fragment, "function")
                    for target, key, value in (
                        (call, "id", get(fragment, "id")),
                        (call["function"], "name", get(function, "name")),
                        (call["function"], "arguments", get(function, "arguments")),
                    ):
                        if value is not None:
                            if not isinstance(value, str):
                                raise ValueError("Invalid tool call fragment")
                            target[key] += value
                    # LiteLLM은 Gemini의 thought signature를 호출 ID에 담을 수 있다.
                    # ID는 불투명 값으로 그대로 전달하며 인자와 같은 유한 크기 제한을 쓴다.
                    if ((self.max_argument_chars is not None and
                         (len(call["function"]["arguments"]) > self.max_argument_chars or len(call["id"]) > self.max_argument_chars))
                            or len(call["function"]["name"]) > 64):
                        raise ValueError("Tool call size limit exceeded")
                reason = get(choice, "finish_reason")
                if reason is not None:
                    if not isinstance(reason, str):
                        raise ValueError("Invalid finish reason")
                    if finish_reason is not None and finish_reason != reason:
                        raise ValueError("Conflicting stream termination")
                    finish_reason = reason
                    if result.finish_reason != reason:
                        result.finish_reason = reason
                        yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
                if content:
                    yield content
        result.usage_complete = bool(result.usage)
        if finish_reason not in ("stop", "tool_calls"):
            raise ValueError("Completion did not finish normally")
        if bool(calls) != (finish_reason == "tool_calls"):
            raise ValueError("Tool calls do not match finish reason")
        ids = [call["id"] for call in calls.values()]
        if (any(not call["id"] or not call["function"]["name"] for call in calls.values())
                or len(set(ids)) != len(ids)):
            raise ValueError("Incomplete or duplicate tool calls")
        if response is not None:
            response.clear()
            response.update(role="assistant", content="".join(content_parts) or None)
            if calls:
                response["tool_calls"] = [calls[index] for index in sorted(calls)]

    def run(self, context: EngineContext) -> Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]:
        """문자열/EngineDelta/마지막 EngineOutput을 yield하거나 EngineOutput/None을 반환한다."""
        if self.action is None:
            raise NotImplementedError("Implement run(context) or provide action=")
        return self.action(context)

    async def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        async with aclosing(self._execute(context, publish_output=True)) as events:
            async for event in events:
                yield event

    async def _execute(self, context: EngineContext, *, publish_output: bool) -> AsyncIterator[EngineEvent]:
        step_id = new_id()
        parts = []
        visibility = None
        result = None
        yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id,
                          kind=self.kind, name=self.name, metadata=deepcopy(self.metadata))
        termination = None
        try:
            deadline = None if self.timeout_seconds is None else asyncio.get_running_loop().time() + self.timeout_seconds
            operation = self.run(context)
            if inspect.isawaitable(operation):
                async with timeout(self.timeout_seconds):
                    result = await operation
                if result is not None and not isinstance(result, EngineOutput):
                    raise TypeError("Return EngineOutput or None, or yield text")
            else:
                try:
                    iterator = operation.__aiter__()
                    while True:
                        remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
                        if remaining is not None and remaining <= 0:
                            raise asyncio.TimeoutError()
                        try:
                            # yield 중에는 서비스가 이벤트를 저장한다. 그 Task를 타이머로
                            # 취소하지 않고 다음 Engine 진행 시 절대 기한을 검사한다.
                            async with timeout(remaining):
                                text = await iterator.__anext__()
                        except StopAsyncIteration:
                            break
                        if isinstance(text, EngineOutput):
                            if result is not None:
                                raise ValueError("Step already has a final output")
                            result = text
                            continue
                        if result is not None:
                            raise ValueError("Cannot emit after a final output")
                        if isinstance(text, EngineEvent) and text.type == EngineEventType.COMPLETION:
                            if text.completion is None:
                                raise ValueError("Completion event requires a result")
                            yield replace(text, step_id=step_id,
                                          completion=replace(text.completion, step_id=step_id))
                            continue
                        if isinstance(text, EngineEvent) and text.type not in EngineEventType._value2member_map_:
                            yield text
                            continue
                        if not isinstance(text, (str, EngineDelta)):
                            raise TypeError("Step streams must yield text, EngineDelta or EngineOutput")
                        if isinstance(text, str) and not text:
                            continue
                        event = self.delta_event(context, text, step_id=step_id)
                        if visibility is not None and visibility != event.delta.visibility:
                            raise ValueError("Step output visibility cannot change during streaming")
                        visibility = event.delta.visibility
                        if event.delta.operation == "replace":
                            parts.clear()
                        parts.append(event.delta.text)
                        yield event
                except (asyncio.CancelledError, GeneratorExit) as error:
                    termination = error
                    raise
                finally:
                    close = getattr(operation, "aclose", None)
                    if close is not None:
                        try:
                            await close()
                        except Exception:
                            if termination is None:
                                raise
                            # 취소/GeneratorExit를 정리 오류로 바꾸지 않는다.
            # 결과 검증도 실패 이벤트/정리 범위 안에 둔다. 입력 객체는 복사 후 연결한다.
            output_context = replace(context, output_visibility=visibility or context.output_visibility)
            completed = self.step_completed_event(output_context, step_id,
                result if result is not None else EngineOutput(text="".join(parts)))
            if visibility is not None and visibility != completed.output.visibility:
                raise ValueError("Step output visibility cannot change at completion")
        except (asyncio.CancelledError, GeneratorExit):
            # Never yield while being cancelled/closed. RunManager finalizes the
            # persisted active Step, including cancellation before run() starts.
            raise
        except Exception as error:
            yield self.step_failed_event(step_id, error, message=f"{self.error_message}: {error}")
            raise
        yield completed
        if publish_output:
            yield self.output_event(output_context, completed.output)
