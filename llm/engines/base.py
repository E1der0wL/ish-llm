"""Engine 이벤트 계약과 BaseEngine의 공통 Step/스트리밍 처리를 정의한다. Engine은 이벤트를 생성하며 영속 파일에는 직접 쓰지 않는다.

Engine contract, Step event lifecycle, and adapter-owned model streaming."""

import asyncio
import inspect
import json
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Protocol, TypeVar, Union

from enum import StrEnum
from contextlib import aclosing
from asyncio import timeout
from llm.core.models import Message, Project, Run, RunStatus, Session, new_id, now
from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef
from llm.errors import stable_error_code
from llm.core.results import CompletionResult, EngineOutput, EngineDelta, ResumeOutput
from llm.core.interactions import InteractionRequest
from llm.core.configuration import UNSET
from llm.core.steering import SteeringMode, SteeringRoute
from llm.components.tools import ToolRegistry
from llm.providers.streaming import CompletionError, CompletionUpdate, CompletionDecoder, CompletionAdapter
from llm.providers.parameters import copy_params
from llm.providers.runtime import diagnostic_context, diagnostic_failure
from llm.services.runtime.usage import current_usage
from llm.policies.errors import ExecutionLimitError


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
    STEERING = "steering"
    STEERING_CHANGED = "steering_changed"


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
    # A completed Step and its resume receipt commit in one service transaction.
    # Only record operations are attachable; initialization remains a separate boundary.
    checkpoint: Optional["EngineEvent"] = None

    def __post_init__(self):
        if self.checkpoint is not None and (self.type != EngineEventType.STEP_COMPLETED
                or not isinstance(self.checkpoint, EngineEvent)
                or self.checkpoint.type != EngineEventType.CHECKPOINT
                or self.checkpoint.metadata.get("operation") != "record"
                or self.checkpoint.checkpoint is not None):
            raise ValueError("A Step completion can attach one checkpoint record")
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


@dataclass(slots=True)
class SteeringInbox:
    """저장 ACK 뒤 서비스가 채우는 호출별 전달함. 영속 상태의 소유자가 아니다."""

    messages: tuple[Message, ...] = ()
    history: dict[str, Message] = field(default_factory=dict)
    target_id: str = "root"
    scope: Optional[str] = None
    node_id: Optional[str] = None
    node_path: Optional[str] = None
    engine: str = ""
    mode: SteeringMode = SteeringMode.UNSUPPORTED
    channels: dict[str, "SteeringInbox"] = field(default_factory=dict, repr=False)
    acknowledged: Optional[str] = None
    opened: bool = False
    closed: bool = False

    def child(self, *, target_id: str, scope: str, engine: str, mode: SteeringMode,
              node_id: str, node_path: Optional[str]) -> "SteeringInbox":
        if not scope or target_id in self.channels:
            raise ValueError("Child instruction channel requires a unique execution and stable scope")
        child = SteeringInbox(history=dict(self.history), target_id=target_id, scope=scope,
                              engine=engine, mode=SteeringMode(mode), node_id=node_id, node_path=node_path,
                              channels=self.channels)
        self.channels[target_id] = child
        return child


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
    # 명시적 재개에서만 조회한 부분 출력. 완료/승인 증명이 아닌 scope별 참고 자료다.
    resume_outputs: tuple[ResumeOutput, ...] = ()
    # 백엔드가 주입하는 실행 정책. Run 전체(중첩 Agent/Graph 포함)가 공유한다.
    tool_scope: Any = None
    pending_work: Any = None
    # 호스트 계산기 레지스트리만 공유한다. 입력 정책은 사용하는 Engine이 별도 사본에 연결한다.
    token_counters: Mapping[str, Callable] = field(default_factory=dict)
    completion_policy: Any = None
    # 중첩 엔진의 최종 결과는 이 Step에 귀속한다. None은 최상위 Run이다.
    output_step_id: Optional[str] = None
    output_visibility: str = "user"
    steering: Optional[SteeringInbox] = None
    # Service-owned, Run-local capability lookup; not persisted and never expands Tool authority.
    capability_resolver: Optional[Callable] = None

    def with_capabilities(self, names, *, expansion=None):
        """Resolve explicitly selected implementation requirements through the owning service."""
        if not isinstance(names, tuple) or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("Capability requirements must be a tuple of names")
        missing = tuple(dict.fromkeys(name for name in names if name not in self.capabilities))
        if not missing:
            return self
        if self.capability_resolver is None:
            raise ValueError(f"Required capabilities unavailable: {missing}")
        resolved = self.capability_resolver(missing, expansion=expansion)
        if not isinstance(resolved, dict) or set(resolved) != set(missing):
            raise ValueError("Capability resolver must return exactly the requested capabilities")
        return replace(self, capabilities={**self.capabilities, **resolved})

    def resolve_config(self, engine: Optional[str] = None) -> dict:
        return self.project.config.resolve_engine_config(self.run.engine if engine is None else engine, self.session.config)

    def execute_tool(self, tool, arguments, *, checkpoint_key: str, result: dict,
                     executor=None, metadata=None, decision=UNSET) -> AsyncIterator[EngineEvent]:
        """기존 invocation key로 Tool을 연결한다. 승인 증명/새 ID를 생성하지 않는다.

        decision 생략 시 waiting Tool approval의 Interaction binding으로만 응답을
        해석한다. Workflow confirmation은 Tool 권한을 부여하지 않는다.
        executor 주입으로 명시된 실행 제한을 유지한다. 소비자는 aclosing을 사용한다.
        """
        from llm.services.runtime.tools import ToolExecutor
        if not isinstance(checkpoint_key, str) or not checkpoint_key.strip():
            raise ValueError("Tool checkpoint_key must be a nonempty string")
        executor = executor if executor is not None else ToolExecutor()
        return executor.execute(tool, arguments, result=result, context=self, metadata=metadata,
                                decision=decision, request_key=checkpoint_key)


class Engine(Protocol):
    """execute는 필수다. 요청 인자는 선택적 동기 for_request(options)로 연결한다.

    factory는 외부 효과 없이 등록 객체를 보존하고 실행별 사본을 반환한다.
    capability는 사본에서 탐색하며 checkpoint 이름과 추가 지시 지원 방식은 유지한다.
    for_request가 없으면 빈 engine_options만 허용한다.
    """
    def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]: ...


def required_capabilities(engine) -> tuple[str, ...]:
    """선언하지 않은 Engine은 외부 기능 없이 실행한다."""
    names = getattr(engine, "required_capabilities", ())
    if (not isinstance(names, tuple) or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Engine required_capabilities must be a tuple of distinct names")
    return names


def steering_mode(engine) -> SteeringMode:
    """잘못된 지원 선언은 조용히 무시하지 않는다."""
    return SteeringMode(getattr(engine, "steering_mode", SteeringMode.UNSUPPORTED))


# 작업 함수를 공통 Step 이벤트와 스트리밍 출력으로 감싼다.
class BaseEngine:
    """Adapt a developer operation to the existing Engine event contract.

    run(context) 또는 action에서 문자열/EngineDelta와 마지막 EngineOutput을 yield한다.
    async 함수는 EngineOutput 또는 None을 반환한다. execute()가 Step과 출력의
    소유권/이벤트를 연결한다. 복잡한 실행기는 execute()에서 아래 출력 메서드를
    재사용한다. 실행별 상태는 인스턴스에 저장하지 않는다.
    요청별 인자가 필요하면 동기 for_request(options)를 구현하여 사본에 연결한다.
    Labels/metadata/error_message are trusted, persistable developer constants.
    """

    required_capabilities = ()
    steering_mode = SteeringMode.UNSUPPORTED

    def validate_selections(self, context):
        """Validate durable Tool selections in this Engine's local checkpoint scope."""
        context.tools.validate_selections(context)

    @staticmethod
    async def instruction_event(context: EngineContext, operation: str, **metadata) -> AsyncIterator[EngineEvent]:
        """서비스 저장 ACK 후에만 계속한다. Engine은 파일이나 영속 상태를 쓰지 않는다."""
        inbox = context.steering
        if inbox is None:
            return
        if inbox.closed:
            raise ValueError("Instruction channel is closed")
        token = new_id()
        yield EngineEvent(EngineEventType.STEERING, metadata={
            **metadata, "operation": operation, "target_id": inbox.target_id, "request_id": token})
        if inbox.acknowledged != token:
            raise RuntimeError("Instruction event requires a persisted service acknowledgement")

    @classmethod
    async def open_instructions(cls, context: EngineContext) -> AsyncIterator[EngineEvent]:
        inbox = context.steering
        if inbox is None or inbox.opened:
            return
        async with aclosing(cls.instruction_event(context, "open", target={
                "id": inbox.target_id, "run_id": context.run.id, "engine": inbox.engine,
                "mode": inbox.mode.value, "scope": inbox.scope, "node_id": inbox.node_id,
                "node_path": inbox.node_path, "accepting": inbox.mode == SteeringMode.CONSUME})) as events:
            async for event in events:
                yield event

    @classmethod
    async def declare_instruction_routes(cls, context: EngineContext, routes) -> AsyncIterator[EngineEvent]:
        """전달 Engine이 검증한 스냅샷 경로를 저장한다. 소비자/Workflow 해석은 서비스에 넣지 않는다."""
        async with aclosing(cls.instruction_event(context, "routes", routes=[r.to_dict() for r in routes])) as events:
            async for event in events:
                yield event

    @classmethod
    async def bind_instruction_route(cls, context: EngineContext, route: SteeringRoute, scope: str) -> AsyncIterator[EngineEvent]:
        """새 노드 실행 경계에서 예약을 결합한다. ACK 전에는 해당 작업을 시작하지 않는다."""
        async with aclosing(cls.instruction_event(context, "bind", route=route.to_dict(), scope=scope)) as events:
            async for event in events:
                yield event

    @classmethod
    async def select_instructions(cls, context: EngineContext, *, checkpoint: str, boundary: str,
                                  final: bool = False, allow_continue: bool = True,
                                  reason: Optional[str] = None) -> AsyncIterator[EngineEvent]:
        """boundary는 엔진 소유의 안정적인 키다. 반복 횟수/노드 의미는 해석하지 않는다."""
        if not isinstance(boundary, str) or not boundary or not isinstance(checkpoint, str) or not checkpoint:
            raise ValueError("Instructions require a checkpoint and nonempty boundary key")
        async with aclosing(cls.instruction_event(context, "select", checkpoint=checkpoint, boundary=boundary,
                final=final, allow_continue=allow_continue, reason=reason)) as events:
            async for event in events:
                yield event

    @classmethod
    async def apply_instructions(cls, context: EngineContext, *, checkpoint: str, boundary: str,
                                 details: Optional[dict] = None) -> AsyncIterator[EngineEvent]:
        """입력 구성·검증을 마친 뒤 호출한다. provider 수신이나 이행을 보증하지 않는다."""
        async with aclosing(cls.instruction_event(context, "apply", checkpoint=checkpoint, boundary=boundary,
                                                  details=details or {})) as events:
            async for event in events:
                yield event

    @classmethod
    async def close_instructions(cls, context: EngineContext, *, reason: str = "completed") -> AsyncIterator[EngineEvent]:
        if context.steering is None or context.steering.closed:
            return
        async with aclosing(cls.instruction_event(context, "close", reason=reason)) as events:
            async for event in events:
                yield event

    def execute_tool(self, context: EngineContext, tool, arguments, *, checkpoint_key: str,
                     result: dict, executor=None, metadata=None, decision=UNSET) -> AsyncIterator[EngineEvent]:
        """공통 문맥에 Tool 호출을 위임한다. Step/승인/retry는 ToolExecutor가 소유한다."""
        return context.execute_tool(tool, arguments, checkpoint_key=checkpoint_key, result=result,
                                    executor=executor, metadata=metadata, decision=decision)

    def __init__(self, name: str = "Step", *, kind: str = "custom",
                 action: Optional[Callable[[EngineContext],
                     Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]]] = None,
                 metadata: Optional[dict] = None,
                 error_message: str = "Step execution failed",
                 completion_fn: Optional[Callable[..., Iterator[Any]]] = None,
                 buffer_size: int = 8,
                 completion_decoder: Optional[Callable[..., CompletionDecoder]] = None,
                 completion_adapter: Optional[CompletionAdapter] = None) -> None:
        if not isinstance(name, str) or not name.strip() or not isinstance(kind, str) or not kind.strip():
            raise ValueError("Step requires a name and kind")
        if action is not None and not callable(action):
            raise TypeError("Step action must be callable")
        if not isinstance(error_message, str) or not error_message.strip():
            raise ValueError("Step error message must be a nonempty string")
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("Step metadata must be a dictionary")
        json.dumps(metadata or {}, allow_nan=False)
        self.name, self.kind = name, kind
        self.action = action
        self.metadata = deepcopy(metadata or {})
        self.error_message = error_message
        if completion_adapter is not None and (completion_fn is not None or completion_decoder is not None):
            raise ValueError("Choose a completion adapter or LiteLLM function/decoder")
        if completion_adapter is None:
            from llm.providers.litellm import LiteLLMCompletionAdapter
            completion_adapter = LiteLLMCompletionAdapter(completion_fn=completion_fn, completion_decoder=completion_decoder)
        self.completion_adapter = completion_adapter
        self.buffer_size = buffer_size
        if type(buffer_size) is not int or buffer_size < 1:
            raise ValueError("buffer_size must be a positive integer")

    def step(self, context: EngineContext,
             action: Callable[[EngineContext], Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]], *,
             name: str, kind: str = "custom", timeout_seconds: Optional[float] = None,
             metadata: Optional[dict] = None,
             error_message: str = "Step execution failed") -> AsyncIterator[EngineEvent]:
        """Wrap one operation when an Engine needs several Steps in execute().

        Consume under aclosing() so early exit closes the operation promptly.
        Only events are emitted; RunManager and StepManager own persistence.
        """
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("Step timeout must be positive and finite, or None")
        return BaseEngine(name, kind=kind, action=action,
                          metadata=metadata, error_message=error_message)._execute(
                              context, publish_output=False, timeout_seconds=timeout_seconds)

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

    async def stream_completion(self, request: Mapping[str, Any], *,
                                response: Optional[dict] = None,
                                include_events: bool = True,
                                provider: Optional[dict] = None,
                                retry_decider=None,
                                recover_completion=None,
                                max_tool_calls: Optional[int] = None,
                                max_argument_chars: Optional[int] = None,
                                max_output_chars: Optional[int] = None) -> AsyncIterator[Union[str, EngineEvent]]:
        """Yield text and completion observations; inherited execute() routes both.

        include_events=False is for standalone text consumers without persistence.
        response receives the assistant message on success only. Observations
        contain no prompts, tool arguments, headers, or arbitrary SDK payloads.
        provider contains only this caller's explicit outer retry/deadline options;
        no Run policy is implicitly inherited and SDK request options stay separate.
        """
        from llm.providers.retry import retry_delay
        from llm.providers.requests import resolve_provider_options, ProviderError
        from llm.policies.retry import RetryPolicy, RetryFailure, select_retry, change_arguments
        # 호출자가 자신의 Project-owned 설정에서 해석한 제한만 이 호출에 전달한다.
        for value in (max_tool_calls, max_argument_chars, max_output_chars):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("Completion limits must be positive integers")
        policy = resolve_provider_options(provider if provider is not None else {})
        adapter = self.completion_adapter
        attempts = adapter.effective_attempts(request, RetryPolicy(policy.get("max_attempts")).attempts)
        retry = RetryPolicy(attempts, policy.get("delay_seconds"), policy.get("max_delay_seconds"),
                            RetryPolicy.end(policy.get("wall_timeout")), "exponential")
        request = self.copy_params(dict(request))
        call_id = new_id()

        def remaining():
            try:
                return retry.remaining()
            except TimeoutError as error:
                raise ProviderError("provider_timeout") from error

        for attempt in range(attempts):
            call_details = {"call_id": call_id, "attempt": attempt + 1, **adapter.describe_request(request)}
            progress = {"received": False}
            invocation = adapter.describe_call(self.copy_params(dict(request)))
            result = CompletionResult(model=invocation.model)
            usage_scope = current_usage()
            if usage_scope is not None:
                try:
                    async with timeout(remaining()):
                        result.reserved_tokens = await usage_scope.reservation(invocation)
                except TimeoutError as error:
                    raise ProviderError("provider_timeout") from error
            started = time.monotonic()
            if include_events:
                yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
            try:
                async with aclosing(self._stream_completion(request, response, result, progress,
                        max_tool_calls, max_argument_chars, max_output_chars)) as stream:
                    while True:
                        try:
                            # 이벤트 저장 중인 소비자는 취소하지 않는다. 다음 진행 시 같은 절대 기한을 검사한다.
                            with diagnostic_context(**call_details):
                                async with timeout(remaining()):
                                    item = await anext(stream)
                        except StopAsyncIteration:
                            break
                        except TimeoutError as error:
                            raise ProviderError("provider_timeout") from error
                        if include_events or isinstance(item, str):
                            yield item
            except (asyncio.CancelledError, GeneratorExit):
                # RunManager finalizes the last durable observation; never yield here.
                raise
            except Exception as error:
                if isinstance(error, (CompletionError, ProviderError)) and getattr(error, "diagnostic", None) is None:
                    error.diagnostic = diagnostic_failure(error, operation="completion",
                        stage="response_validation" if isinstance(error, CompletionError) else "deadline",
                        **call_details, response_received=progress["received"],
                        finish_reason=result.finish_reason, response_id=result.response_id)
                result.status = RunStatus.FAILED
                result.ended_at = now()
                result.duration_seconds = time.monotonic() - started
                if include_events:
                    yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
                if progress["received"] or not retry.allows(attempt + 1):
                    raise
                failure = Diagnostic.from_exception(error)
                if recover_completion is not None and failure.classification.reason == "context":
                    try:
                        async with timeout(remaining()):
                            repaired = await recover_completion(failure, self.copy_params(request), attempt + 1)
                    except asyncio.CancelledError:
                        raise
                    except Exception as recovery_error:
                        # A rejected proposal/analysis failure cannot erase the provider cause.
                        from llm.providers.runtime import diagnostic
                        diagnostic("completion_recovery_rejected", operation="completion", **call_details,
                                   reason="deadline" if isinstance(recovery_error, TimeoutError) else "proposal_failed",
                                   exception_type=f"{type(recovery_error).__module__}.{type(recovery_error).__qualname__}")
                        raise error
                    if repaired is None:
                        raise
                    remaining()  # The callback cannot renew this invocation's deadline.
                    request = repaired
                    from llm.providers.runtime import diagnostic
                    diagnostic("completion_recovery", operation="completion", **{**call_details, "attempt": attempt + 2})
                    continue
                if not adapter.retryable(error):
                    raise
                try:
                    async with timeout(remaining()):
                        selected = await select_retry(RetryFailure("completion", self.copy_params(request),
                                adapter.classify_error(error), adapter.classify_error(error).replace("_", " "), attempt + 1, True,
                            adapter.retry_fields(request)), retry_decider)
                except TimeoutError as timeout_error:
                    raise ProviderError("provider_timeout") from timeout_error
                if selected is None:
                    raise
                # Transcript/Tool authority/input budget은 이미 준비됐다. transport만 수정한다.
                request = change_arguments(request, selected, allowed=adapter.retry_fields(request), copy=self.copy_params,
                    validate=lambda value: adapter.validate_retry(value, original=request))
                try:
                    delay = retry_delay(retry, attempt + 1, error, policy)
                    if delay is None:
                        from llm.providers.runtime import diagnostic
                        diagnostic("provider_retry_stopped", operation="completion", **call_details,
                                   reason="retry_after_exceeds_max_delay")
                        raise error
                except TimeoutError as timeout_error:
                    raise ProviderError("provider_timeout") from timeout_error
                from llm.providers.observations import record_retry
                # 확정된 대기 시작을 알린다. UI의 countdown/취소 관찰 계약을 유지한다.
                record_retry("completion")
                from llm.providers.runtime import diagnostic
                diagnostic("provider_retry", operation="completion", call_id=call_id,
                           attempt=attempt + 2, delay_seconds=delay, modified=bool(selected.changes))
                await asyncio.sleep(delay)
                continue
            else:
                result.status = RunStatus.COMPLETED
                result.ended_at = now()
                result.duration_seconds = time.monotonic() - started
                if include_events:
                    yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))

                return

    async def _stream_completion(self, request: Mapping[str, Any], response: Optional[dict],
                                 result: CompletionResult, progress: dict,
                                 max_tool_calls, max_argument_chars, max_output_chars) -> AsyncIterator[Union[str, EngineEvent]]:
        params = self.completion_adapter.prepare(self.copy_params(dict(request)))
        if response is not None and not isinstance(response, dict):
            raise TypeError("Completion response must be a dictionary")
        decoder = self.completion_adapter.decoder(max_tool_calls=max_tool_calls,
            max_argument_chars=max_argument_chars, max_output_chars=max_output_chars)
        async with aclosing(self.completion_adapter.stream(
            params, buffer_size=self.buffer_size,
        )) as chunks:
            async for chunk in chunks:
                # Even a chunk with no visible text forbids replay of this attempt.
                progress["received"] = True
                for update in decoder.decode(chunk):
                    if not isinstance(update, CompletionUpdate):
                        raise TypeError("Completion decoder must yield CompletionUpdate")
                    changed = False
                    for key in ("response_id", "model", "finish_reason"):
                        value = getattr(update, key)
                        if value is not None and value != getattr(result, key):
                            setattr(result, key, value)
                            changed = True
                    if update.usage:
                        usage = {**result.usage, **deepcopy(update.usage)}
                        if usage != result.usage:
                            result.usage = usage
                            changed = True
                    if update.reasoning:
                        result.reasoning_content += update.reasoning
                        changed = True
                    if changed:
                        yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
                    if update.text:
                        yield update.text
        result.usage_complete = bool(result.usage)
        message = decoder.finish()
        if not isinstance(message, dict):
            raise TypeError("Completion decoder must return a message object")
        if response is not None:
            message = deepcopy(message)
            response.clear()
            response.update(message)

    def run(self, context: EngineContext) -> Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]:
        """문자열/EngineDelta/마지막 EngineOutput을 yield하거나 EngineOutput/None을 반환한다."""
        if self.action is None:
            raise NotImplementedError("Implement run(context) or provide action=")
        return self.action(context)

    async def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        async with aclosing(self._execute(context, publish_output=True)) as events:
            async for event in events:
                yield event

    async def _execute(self, context: EngineContext, *, publish_output: bool, timeout_seconds=None) -> AsyncIterator[EngineEvent]:
        step_id = new_id()
        parts = []
        visibility = None
        result = None
        yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id,
                          kind=self.kind, name=self.name, metadata=deepcopy(self.metadata))
        termination = None
        guard = None
        try:
            deadline = None if timeout_seconds is None else asyncio.get_running_loop().time() + timeout_seconds
            with diagnostic_context(step_id=step_id):
                operation = self.run(context)
            if inspect.isawaitable(operation):
                with diagnostic_context(step_id=step_id):
                    guard = timeout(timeout_seconds)
                    async with guard:
                        result = await operation
                if result is not None and not isinstance(result, EngineOutput):
                    raise TypeError("Return EngineOutput or None, or yield text")
            else:
                try:
                    iterator = operation.__aiter__()
                    while True:
                        remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
                        if remaining is not None and remaining <= 0:
                            raise ExecutionLimitError("step_timeout", "Step execution deadline exceeded")
                        try:
                            # yield 중에는 서비스가 이벤트를 저장한다. 그 Task를 타이머로
                            # 취소하지 않고 다음 Engine 진행 시 절대 기한을 검사한다.
                            with diagnostic_context(step_id=step_id):
                                guard = timeout(remaining)
                                async with guard:
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
                        if isinstance(text, EngineEvent) and text.type == EngineEventType.STEERING:
                            yield replace(text, step_id=step_id)
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
            # 소유한 기한만 분류한다. action이 직접 던진 TimeoutError를 wrapper timeout으로 오인하지 않는다.
            failure = (ExecutionLimitError("step_timeout", "Step execution deadline exceeded")
                       if isinstance(error, TimeoutError) and guard is not None and guard.expired() else error)
            yield self.step_failed_event(step_id, failure, message=f"{self.error_message}: {failure}")
            if failure is not error:
                raise failure from error
            raise
        yield completed
        if publish_output:
            yield self.output_event(output_context, completed.output)
