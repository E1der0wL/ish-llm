"""Engine 이벤트 계약과 BaseEngine의 공통 Step/스트리밍 처리를 정의한다. Engine은 이벤트를 생성하며 영속 파일에는 직접 쓰지 않는다.

Engine contract, Step event lifecycle, and adapter-owned model streaming."""

from llm.core.models import ResourceKind, StepKind

from llm.core.checkpoints import CheckpointOperation
from llm.core.steering_channel import SteeringChannel
from llm.core.steering import SteeringTarget
from llm.core.steering_events import SteeringEventData, SteeringOperation, SteeringReason
from llm.core.checkpoint_records import CheckpointWrite

from llm.core.models import StepErrorCode

from llm.core.results import OutputOperation, OutputVisibility, OutputContract

from llm.providers.codes import ProviderErrorCode
import asyncio
import inspect
import json
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from copy import copy, deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Optional, Protocol, TypeVar, Union

from enum import StrEnum
from contextlib import aclosing
from asyncio import timeout
from llm.core.models import Message, Project, Run, RunStatus, Session, new_id, now
from llm.core.agents import AgentDefinition
from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef, DiagnosticObservation, ErrorReason, ExceptionType
from llm.errors import stable_error_code
from llm.core.results import CompletionResult, EngineOutput, EngineDelta, ResumeOutput
from llm.core.interactions import InteractionRequest
from llm.core.configuration import UNSET
from llm.core.steering import SteeringMode, SteeringRoute
from .contracts import EngineContextBinding, required_capabilities, steering_mode, agent_mode
from .events import EngineEvent, EngineEventType
from .context import EngineContext, SteeringInbox
from llm.components.tools import ToolRegistry
from llm.providers.streaming import CompletionError, CompletionAdapter
from llm.core.model_protocol import CompletionUpdate, ModelRequest, ModelResponse, ModelOperation, copy_model
from llm.providers.diagnostic_values import ProviderAttempt, ProviderRetryObservation, ProviderDiagnosticText
from .completion_state import CompletionProgress
from llm.providers.runtime import diagnostic_context, diagnostic_failure
from llm.services.runtime.usage import current_usage
from llm.policies.errors import ExecutionLimitError


_OutputValue = TypeVar("_OutputValue", EngineDelta, EngineOutput)




class Engine(Protocol):
    """execute와 동기 for_agent(definition)가 Agent 실행 계약이다.

    factory는 외부 효과 없이 등록 객체를 보존하고 실행별 사본을 반환한다.
    capability는 사본에서 탐색하며 checkpoint 이름과 추가 지시 지원 방식은 유지한다.
    요청 환경을 받는 구현은 별도의 동기 for_context(context)를 선언한다.
    """
    def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]: ...


# 작업 함수를 공통 Step 이벤트와 스트리밍 출력으로 감싼다.
class BaseEngine:
    """Adapt a developer operation to the existing Engine event contract.

    run(context) 또는 action에서 문자열/EngineDelta와 마지막 EngineOutput을 yield한다.
    async 함수는 EngineOutput 또는 None을 반환한다. execute()가 Step과 출력의
    소유권/이벤트를 연결한다. 복잡한 실행기는 execute()에서 아래 출력 메서드를
    재사용한다. 실행별 상태는 인스턴스에 저장하지 않는다.
    Agent 설정은 동기 for_agent(definition)가 검증하여 사본에 연결한다.
    Labels/metadata/error_message are trusted, persistable developer constants.
    """

    required_capabilities = ()
    ABSENT = None
    INCLUDE_COMPLETION_EVENTS = True
    RETRYABLE_REQUEST = True
    MINIMUM_COMPLETION_LIMIT = 1
    INVALID_MODEL_REQUEST = 'Completion requires ModelRequest'
    INVALID_RESPONSE_CAPTURE = 'Completion response capture requires a list'
    INVALID_COMPLETION_LIMIT = 'Completion limits must be positive integers'
    INVALID_RECOVERED_REQUEST = 'Completion recovery must return ModelRequest'
    INVALID_PREPARED_REQUEST = 'Completion adapter must prepare ModelRequest'
    INVALID_COMPLETION_UPDATE = 'Completion decoder must yield CompletionUpdate'
    INVALID_MODEL_RESPONSE = 'Completion decoder must return ModelResponse'
    DEFAULT_NAME = "Step"
    DEFAULT_KIND = "custom"
    DEFAULT_ERROR_MESSAGE = "Step execution failed"
    DEFAULT_BUFFER_SIZE = 8
    MINIMUM_BUFFER_SIZE = 1
    EXPIRED_TIMEOUT = 0
    JSON_ALLOW_NAN = False
    PUBLISH_OUTPUT = True
    STEP_ONLY = False
    INTERMEDIATE_BOUNDARY = False
    FINAL_BOUNDARY = True
    ALLOW_CONTINUE = True
    CLOSE_METHOD = "aclose"
    ERROR_SEPARATOR = ": "
    CLOSED_INSTRUCTIONS = 'Instruction channel is closed'
    MISSING_INSTRUCTION_ACK = 'Instruction event requires a persisted service acknowledgement'
    INVALID_INSTRUCTION_BOUNDARY = 'Instructions require a checkpoint and nonempty boundary key'
    INVALID_STEP_NAME = 'Step requires a name and kind'
    INVALID_ACTION = 'Step action must be callable'
    INVALID_ERROR_MESSAGE = 'Step error message must be a nonempty string'
    INVALID_METADATA = 'Step metadata must be a dictionary'
    INVALID_BUFFER_SIZE = 'buffer_size must be a positive integer'
    INVALID_TIMEOUT = 'Step timeout must be positive and finite, or None'
    INVALID_OUTPUT_VALUE = 'Expected EngineDelta or EngineOutput'
    INVALID_OUTPUT_VISIBILITY = 'Invalid context output visibility'
    INVALID_DELTA = 'delta_event requires text or EngineDelta'
    INVALID_FINAL_OUTPUT = 'output_event requires a final EngineOutput'
    MISSING_FAILED_STEP = 'A failed Step requires an ID'
    MISSING_COMPLETED_STEP = 'A completed Step requires an ID'
    INVALID_COMPLETION_OUTPUT = 'Step completion requires a final EngineOutput'
    MISSING_ACTION = 'Implement run(context) or provide action='
    INVALID_ACTION_RESULT = 'Return EngineOutput or None, or yield text'
    TIMEOUT_MESSAGE = 'Step execution deadline exceeded'
    DUPLICATE_OUTPUT = 'Step already has a final output'
    LATE_OUTPUT = 'Cannot emit after a final output'
    MISSING_COMPLETION_RESULT = 'Completion event requires a result'
    INVALID_STREAM_VALUE = 'Step streams must yield text, EngineDelta or EngineOutput'
    CHANGED_STREAM_VISIBILITY = 'Step output visibility cannot change during streaming'
    CHANGED_FINAL_VISIBILITY = 'Step output visibility cannot change at completion'
    HOST_AGENT_UNSUPPORTED = frozenset((AgentDefinition.Field.SYSTEM_PROMPT.value,
        AgentDefinition.Field.COMPLETION.value, AgentDefinition.Field.ENGINE_OPTIONS.value))
    HOST_AGENT_INVALID = "Host action must implement these Agent settings: "
    HOST_FIELD_SEPARATOR = ", "
    steering_mode = SteeringMode.UNSUPPORTED

    def for_agent(self, definition):
        """Bind a host action without silently accepting unimplemented settings."""
        AgentDefinition.validate_selected(definition)
        unsupported = set(definition) & BaseEngine.HOST_AGENT_UNSUPPORTED
        if unsupported:
            raise ValueError(BaseEngine.HOST_AGENT_INVALID + BaseEngine.HOST_FIELD_SEPARATOR.join(sorted(unsupported)))
        return copy(self)

    def validate_selections(self, context):
        """Validate durable Tool selections in this Engine's local checkpoint scope."""
        context.tools.validate_selections(context)

    @staticmethod
    async def instruction_event(context: EngineContext, operation: str, **metadata) -> AsyncIterator[EngineEvent]:
        """서비스 저장 ACK 후에만 계속한다. Engine은 파일이나 영속 상태를 쓰지 않는다."""
        inbox = context.steering
        if inbox is BaseEngine.ABSENT:
            return
        if inbox.closed:
            raise ValueError(BaseEngine.CLOSED_INSTRUCTIONS)
        token = new_id()
        yield EngineEvent(EngineEventType.STEERING, metadata=SteeringEventData.request(operation, inbox.target_id, token, metadata).to_dict())
        if inbox.acknowledged != token:
            raise RuntimeError(BaseEngine.MISSING_INSTRUCTION_ACK)

    @classmethod
    async def open_instructions(cls, context: EngineContext) -> AsyncIterator[EngineEvent]:
        inbox = context.steering
        if inbox is BaseEngine.ABSENT or inbox.opened:
            return
        async with aclosing(cls.instruction_event(context, SteeringOperation.OPEN, target=SteeringTarget(
                id=inbox.target_id, run_id=context.run.id, engine=inbox.engine,
                mode=inbox.mode, scope=inbox.scope, node_id=inbox.node_id,
                node_path=inbox.node_path, accepting=inbox.mode == SteeringMode.CONSUME).registration())) as events:
            async for event in events:
                yield event

    @classmethod
    async def declare_instruction_routes(cls, context: EngineContext, routes) -> AsyncIterator[EngineEvent]:
        """전달 Engine이 검증한 스냅샷 경로를 저장한다. 소비자/Workflow 해석은 서비스에 넣지 않는다."""
        async with aclosing(cls.instruction_event(context, SteeringOperation.ROUTES, routes=[r.to_dict() for r in routes])) as events:
            async for event in events:
                yield event

    @classmethod
    async def bind_instruction_route(cls, context: EngineContext, route: SteeringRoute, scope: str) -> AsyncIterator[EngineEvent]:
        """새 노드 실행 경계에서 예약을 결합한다. ACK 전에는 해당 작업을 시작하지 않는다."""
        async with aclosing(cls.instruction_event(context, SteeringOperation.BIND, route=route.to_dict(), scope=scope)) as events:
            async for event in events:
                yield event

    @classmethod
    async def select_instructions(cls, context: EngineContext, *, checkpoint: str, boundary: str,
                                  final: bool = INTERMEDIATE_BOUNDARY, allow_continue: bool = ALLOW_CONTINUE,
                                  reason: Optional[str] = ABSENT) -> AsyncIterator[EngineEvent]:
        """boundary는 엔진 소유의 안정적인 키다. 반복 횟수/노드 의미는 해석하지 않는다."""
        if not isinstance(boundary, str) or not boundary or not isinstance(checkpoint, str) or not checkpoint:
            raise ValueError(BaseEngine.INVALID_INSTRUCTION_BOUNDARY)
        async with aclosing(cls.instruction_event(context, SteeringOperation.SELECT, checkpoint=checkpoint, boundary=boundary,
                final=final, allow_continue=allow_continue, reason=reason)) as events:
            async for event in events:
                yield event

    @classmethod
    async def apply_instructions(cls, context: EngineContext, *, checkpoint: str, boundary: str,
                                 details: Optional[dict] = ABSENT) -> AsyncIterator[EngineEvent]:
        """입력 구성·검증을 마친 뒤 호출한다. provider 수신이나 이행을 보증하지 않는다."""
        async with aclosing(cls.instruction_event(context, SteeringOperation.APPLY, checkpoint=checkpoint, boundary=boundary,
                                                  details=details or {})) as events:
            async for event in events:
                yield event

    @classmethod
    async def close_instructions(cls, context: EngineContext, *, reason: str = SteeringReason.COMPLETED) -> AsyncIterator[EngineEvent]:
        if context.steering is BaseEngine.ABSENT or context.steering.closed:
            return
        async with aclosing(cls.instruction_event(context, SteeringOperation.CLOSE, reason=reason)) as events:
            async for event in events:
                yield event

    def execute_tool(self, context: EngineContext, tool, arguments, *, checkpoint_key: str,
                     result: dict, executor=ABSENT, metadata=ABSENT, decision=UNSET) -> AsyncIterator[EngineEvent]:
        """공통 문맥에 Tool 호출을 위임한다. Step/승인/retry는 ToolExecutor가 소유한다."""
        return context.execute_tool(tool, arguments, checkpoint_key=checkpoint_key, result=result,
                                    executor=executor, metadata=metadata, decision=decision)

    def __init__(self, name: str = DEFAULT_NAME, *, kind: str = DEFAULT_KIND,
                 action: Optional[Callable[[EngineContext],
                     Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]]] = ABSENT,
                 metadata: Optional[dict] = ABSENT,
                 error_message: str = DEFAULT_ERROR_MESSAGE,
                 buffer_size: int = DEFAULT_BUFFER_SIZE,
                 completion_adapter: Optional[CompletionAdapter] = ABSENT) -> None:
        if not isinstance(name, str) or not name.strip() or not isinstance(kind, str) or not kind.strip():
            raise ValueError(BaseEngine.INVALID_STEP_NAME)
        if action is not BaseEngine.ABSENT and not callable(action):
            raise TypeError(BaseEngine.INVALID_ACTION)
        if not isinstance(error_message, str) or not error_message.strip():
            raise ValueError(BaseEngine.INVALID_ERROR_MESSAGE)
        if metadata is not BaseEngine.ABSENT and not isinstance(metadata, dict):
            raise TypeError(BaseEngine.INVALID_METADATA)
        json.dumps(metadata or {}, allow_nan=BaseEngine.JSON_ALLOW_NAN)
        self.name, self.kind = name, kind
        self.action = action
        self.metadata = deepcopy(metadata or {})
        self.error_message = error_message
        if completion_adapter is BaseEngine.ABSENT:
            from llm.providers.litellm import LiteLLMCompletionAdapter
            completion_adapter = LiteLLMCompletionAdapter()
        self.completion_adapter = completion_adapter
        self.buffer_size = buffer_size
        if type(buffer_size) is not int or buffer_size < BaseEngine.MINIMUM_BUFFER_SIZE:
            raise ValueError(BaseEngine.INVALID_BUFFER_SIZE)

    def step(self, context: EngineContext,
             action: Callable[[EngineContext], Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]], *,
             name: str, kind: str = DEFAULT_KIND, timeout_seconds: Optional[float] = ABSENT,
             metadata: Optional[dict] = ABSENT,
             error_message: str = DEFAULT_ERROR_MESSAGE) -> AsyncIterator[EngineEvent]:
        """Wrap one operation when an Engine needs several Steps in execute().

        Consume under aclosing() so early exit closes the operation promptly.
        Only events are emitted; RunManager and StepManager own persistence.
        """
        if timeout_seconds is not BaseEngine.ABSENT and (
            isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= BaseEngine.EXPIRED_TIMEOUT
        ):
            raise ValueError(BaseEngine.INVALID_TIMEOUT)
        return BaseEngine(name, kind=kind, action=action,
                          metadata=metadata, error_message=error_message)._execute(
                              context, publish_output=BaseEngine.STEP_ONLY, timeout_seconds=timeout_seconds)

    @staticmethod
    def _bind_output(context: EngineContext, value: _OutputValue, *,
                     step_id: Optional[str] = ABSENT) -> _OutputValue:
        """호출별 소유권을 적용한다. 내부 출력은 외부 표시 범위로 승격하지 않는다."""
        if not isinstance(value, (EngineDelta, EngineOutput)):
            raise TypeError(BaseEngine.INVALID_OUTPUT_VALUE)
        if context.output_visibility not in (OutputVisibility.USER, OutputVisibility.INTERNAL):
            raise ValueError(BaseEngine.INVALID_OUTPUT_VISIBILITY)
        owner = context.output_step_id if step_id is BaseEngine.ABSENT else step_id
        visibility = OutputVisibility.INTERNAL if OutputVisibility.INTERNAL in (context.output_visibility, value.visibility) else OutputVisibility.USER
        return replace(value, output_id=owner or context.run.id, step_id=owner,
                       visibility=visibility, sequence=OutputContract.INITIAL_SEQUENCE)

    @staticmethod
    def delta_event(context: EngineContext, value: Union[str, EngineDelta], *,
                    step_id: Optional[str] = ABSENT) -> EngineEvent:
        """문자열/델타를 현재 Run 또는 명시한 Step의 변경 이벤트로 만든다."""
        if isinstance(value, str):
            value = EngineDelta(context.run.id, text=value)
        if not isinstance(value, EngineDelta):
            raise TypeError(BaseEngine.INVALID_DELTA)
        delta = BaseEngine._bind_output(context, value, step_id=step_id)
        return EngineEvent(EngineEventType.TEXT_DELTA, step_id=delta.step_id, delta=delta)

    @staticmethod
    def output_event(context: EngineContext, output: EngineOutput) -> EngineEvent:
        """최상위 결과는 Run, 중첩 결과는 context.output_step_id에 연결한다."""
        if not isinstance(output, EngineOutput) or not output.final:
            raise ValueError(BaseEngine.INVALID_FINAL_OUTPUT)
        output = BaseEngine._bind_output(context, output)
        return EngineEvent(EngineEventType.OUTPUT, step_id=output.step_id, output=output)

    @staticmethod
    def step_failed_event(step_id: str, error: Exception, *, message: Optional[str] = ABSENT,
                          diagnostic: Optional[Diagnostic] = ABSENT, metadata: Optional[dict] = ABSENT,
                          code: str = StepErrorCode.FAILED) -> EngineEvent:
        """실패 의미를 전달한다. 명시 진단/기존 진단은 유지하고 누락 source만 Step에 연결한다."""
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError(BaseEngine.MISSING_FAILED_STEP)
        source = ResourceRef(ResourceKind.STEP, step_id, step_id=step_id)
        if diagnostic is BaseEngine.ABSENT:
            diagnostic = Diagnostic.from_exception(error, code=code, source=source)
        elif isinstance(diagnostic, Diagnostic) and diagnostic.source is BaseEngine.ABSENT:
            diagnostic = replace(diagnostic, source=source)
        return EngineEvent(EngineEventType.STEP_FAILED, step_id=step_id,
            error=str(error) if message is BaseEngine.ABSENT else message, diagnostic=deepcopy(diagnostic),
            metadata=deepcopy(metadata or {}), failure_code=stable_error_code(error))

    @staticmethod
    def step_completed_event(context: EngineContext, step_id: str, output: EngineOutput, *,
                             metadata: Optional[dict] = ABSENT) -> EngineEvent:
        """Step 결과와 완료를 함께 전달한다. Step 시작/실행 순서는 호출자가 정한다."""
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError(BaseEngine.MISSING_COMPLETED_STEP)
        if not isinstance(output, EngineOutput) or not output.final:
            raise ValueError(BaseEngine.INVALID_COMPLETION_OUTPUT)
        output = BaseEngine._bind_output(context, output, step_id=step_id)
        return EngineEvent(EngineEventType.STEP_COMPLETED, step_id=step_id, output=output,
                           metadata=deepcopy(metadata or {}))


    async def stream_completion(self, request: ModelRequest, *,
                                response: Optional[list[ModelResponse]] = ABSENT,
                                include_events: bool = INCLUDE_COMPLETION_EVENTS,
                                provider: Optional[dict] = ABSENT,
                                retry_decider=ABSENT,
                                recover_completion=ABSENT,
                                max_tool_calls: Optional[int] = ABSENT,
                                max_argument_chars: Optional[int] = ABSENT,
                                max_output_chars: Optional[int] = ABSENT) -> AsyncIterator[Union[str, EngineEvent]]:
        """Yield text and completion observations; inherited execute() routes both.

        include_events=False is for standalone text consumers without persistence.
        response receives one common response on success only. Observations
        contain no prompts, tool arguments, headers, or arbitrary SDK payloads.
        provider contains only this caller's explicit outer retry/deadline options;
        no Run policy is implicitly inherited and SDK request options stay separate.
        """
        from llm.providers.retry import retry_delay
        from llm.providers.options import ProviderOptions
        from llm.providers.requests import resolve_provider_options, ProviderError
        from llm.policies.retry import RetryBackoff, RetryPolicy, RetryFailure, select_retry, change_arguments
        if not isinstance(request, ModelRequest):
            raise TypeError(BaseEngine.INVALID_MODEL_REQUEST)
        if response is not BaseEngine.ABSENT and not isinstance(response, list):
            raise TypeError(BaseEngine.INVALID_RESPONSE_CAPTURE)
        # 호출자가 자신의 Project-owned 설정에서 해석한 제한만 이 호출에 전달한다.
        for value in (max_tool_calls, max_argument_chars, max_output_chars):
            if value is not BaseEngine.ABSENT and (type(value) is not int or value < BaseEngine.MINIMUM_COMPLETION_LIMIT):
                raise ValueError(BaseEngine.INVALID_COMPLETION_LIMIT)
        policy = resolve_provider_options(provider if provider is not BaseEngine.ABSENT else {})
        provider_policy = ProviderOptions(policy)
        adapter = self.completion_adapter
        attempts = adapter.effective_attempts(request, RetryPolicy(provider_policy.max_attempts).attempts)
        retry = RetryPolicy(attempts, provider_policy.delay_seconds, provider_policy.max_delay_seconds,
                            RetryPolicy.end(provider_policy.wall_timeout), RetryBackoff.EXPONENTIAL)
        request = copy_model(request)
        call_id = new_id()

        def remaining():
            try:
                return retry.remaining()
            except TimeoutError as error:
                raise ProviderError(ProviderErrorCode.TIMEOUT.value) from error

        for attempt in range(attempts):
            observation = ProviderAttempt.from_index(call_id, attempt)
            call_details = observation.details(adapter.describe_request(request))
            progress = CompletionProgress()
            invocation = adapter.describe_call(copy_model(request))
            result = CompletionResult(model=invocation.model)
            usage_scope = current_usage()
            if usage_scope is not BaseEngine.ABSENT:
                try:
                    async with timeout(remaining()):
                        result.reserved_tokens = await usage_scope.reservation(invocation)
                except TimeoutError as error:
                    raise ProviderError(ProviderErrorCode.TIMEOUT.value) from error
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
                            raise ProviderError(ProviderErrorCode.TIMEOUT.value) from error
                        if include_events or isinstance(item, str):
                            yield item
            except (asyncio.CancelledError, GeneratorExit):
                # RunManager finalizes the last durable observation; never yield here.
                raise
            except Exception as error:
                if isinstance(error, (CompletionError, ProviderError)) and getattr(error, DiagnosticObservation.ATTRIBUTE, BaseEngine.ABSENT) is BaseEngine.ABSENT:
                    error.diagnostic = diagnostic_failure(error, operation=ModelOperation.COMPLETION.value,
                        stage=ProviderRetryObservation.RESPONSE_VALIDATION if isinstance(error, CompletionError) else ProviderRetryObservation.DEADLINE,
                        **call_details, response_received=progress.received,
                        raw_finish_reason=result.finish_reason, termination=result.termination,
                        response_id=result.response_id)
                result.status = RunStatus.FAILED
                result.ended_at = now()
                result.duration_seconds = time.monotonic() - started
                if include_events:
                    yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
                if progress.received or not retry.allows(observation.attempt):
                    raise
                failure = Diagnostic.from_exception(error)
                if recover_completion is not BaseEngine.ABSENT and failure.classification.reason == ErrorReason.CONTEXT:
                    try:
                        async with timeout(remaining()):
                            repaired = await recover_completion(failure, copy_model(request), observation.attempt)
                    except asyncio.CancelledError:
                        raise
                    except Exception as recovery_error:
                        # A rejected proposal/analysis failure cannot erase the provider cause.
                        from llm.providers.runtime import diagnostic
                        diagnostic(ProviderRetryObservation.RECOVERY_REJECTED, operation=ModelOperation.COMPLETION.value, **call_details,
                                   reason=ProviderRetryObservation.DEADLINE if isinstance(recovery_error, TimeoutError) else ProviderRetryObservation.PROPOSAL_FAILED,
                                   exception_type=ExceptionType.from_exception(recovery_error).type)
                        raise error
                    if repaired is BaseEngine.ABSENT:
                        raise
                    if not isinstance(repaired, ModelRequest):
                        raise TypeError(BaseEngine.INVALID_RECOVERED_REQUEST)
                    remaining()  # The callback cannot renew this invocation's deadline.
                    request = repaired
                    from llm.providers.runtime import diagnostic
                    diagnostic(ProviderRetryObservation.RECOVERY, operation=ModelOperation.COMPLETION.value, **observation.next_details(call_details))
                    continue
                if not adapter.retryable(error):
                    raise
                try:
                    async with timeout(remaining()):
                        selected = await select_retry(RetryFailure(ModelOperation.COMPLETION.value, copy_model(request.parameters),
                                adapter.classify_error(error), ProviderDiagnosticText.describe(adapter.classify_error(error)), observation.attempt, BaseEngine.RETRYABLE_REQUEST,
                            adapter.retry_fields(request)), retry_decider)
                except TimeoutError as timeout_error:
                    raise ProviderError(ProviderErrorCode.TIMEOUT.value) from timeout_error
                if selected is BaseEngine.ABSENT:
                    raise
                # Transcript/Tool authority/input budget은 이미 준비됐다. transport만 수정한다.
                parameters = change_arguments(request.parameters, selected,
                    allowed=adapter.retry_fields(request), copy=copy_model,
                    validate=lambda value: adapter.validate_retry(replace(request, parameters=value), original=request))
                request = replace(request, parameters=parameters)
                try:
                    delay = retry_delay(retry, observation.attempt, error, policy)
                    if delay is BaseEngine.ABSENT:
                        from llm.providers.runtime import diagnostic
                        diagnostic(ProviderRetryObservation.STOPPED, operation=ModelOperation.COMPLETION.value, **call_details,
                                   reason=ProviderRetryObservation.EXCEEDED_DELAY)
                        raise error
                except TimeoutError as timeout_error:
                    raise ProviderError(ProviderErrorCode.TIMEOUT.value) from timeout_error
                from llm.providers.observations import record_retry
                # 확정된 대기 시작을 알린다. UI의 countdown/취소 관찰 계약을 유지한다.
                record_retry(ModelOperation.COMPLETION.value)
                from llm.providers.runtime import diagnostic
                diagnostic(ProviderRetryObservation.RETRY, operation=ModelOperation.COMPLETION.value, call_id=call_id,
                           attempt=observation.next_attempt, delay_seconds=delay, modified=bool(selected.changes))
                await asyncio.sleep(delay)
                continue
            else:
                result.status = RunStatus.COMPLETED
                result.ended_at = now()
                result.duration_seconds = time.monotonic() - started
                if include_events:
                    yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))

                return

    async def _stream_completion(self, request: ModelRequest, response: Optional[list[ModelResponse]],
                                 result: CompletionResult, progress: CompletionProgress,
                                 max_tool_calls, max_argument_chars, max_output_chars) -> AsyncIterator[Union[str, EngineEvent]]:
        params = self.completion_adapter.prepare(copy_model(request))
        if not isinstance(params, ModelRequest):
            raise TypeError(BaseEngine.INVALID_PREPARED_REQUEST)
        decoder = self.completion_adapter.decoder(max_tool_calls=max_tool_calls,
            max_argument_chars=max_argument_chars, max_output_chars=max_output_chars)
        async with aclosing(self.completion_adapter.stream(
            params, buffer_size=self.buffer_size,
        )) as chunks:
            async for chunk in chunks:
                # Even a chunk with no visible text forbids replay of this attempt.
                progress.accept_chunk()
                for update in decoder.decode(chunk):
                    if not isinstance(update, CompletionUpdate):
                        raise TypeError(BaseEngine.INVALID_COMPLETION_UPDATE)
                    if result.observe_update(update):
                        yield EngineEvent(EngineEventType.COMPLETION, completion=deepcopy(result))
                    if update.text:
                        yield update.text
        # A fully drained stream can report actual usage even when its final
        # response is rejected. Interrupted streams never reach this boundary.
        result.observe_drained_usage()
        answer = decoder.finish()
        if not isinstance(answer, ModelResponse):
            raise TypeError(BaseEngine.INVALID_MODEL_RESPONSE)
        # A decoder may provide final observations only at finish. Missing usage
        # remains unknown and must never release a token reservation as zero.
        result.observe_response(answer)
        if response is not BaseEngine.ABSENT:
            response.clear()
            response.append(copy_model(answer))

    def run(self, context: EngineContext) -> Union[AsyncIterator[Union[str, EngineDelta, EngineEvent, EngineOutput]], Awaitable[Optional[EngineOutput]]]:
        """문자열/EngineDelta/마지막 EngineOutput을 yield하거나 EngineOutput/None을 반환한다."""
        if self.action is BaseEngine.ABSENT:
            raise NotImplementedError(BaseEngine.MISSING_ACTION)
        return self.action(context)

    async def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        async with aclosing(self._execute(context, publish_output=BaseEngine.PUBLISH_OUTPUT)) as events:
            async for event in events:
                yield event

    async def _execute(self, context: EngineContext, *, publish_output: bool, timeout_seconds=ABSENT) -> AsyncIterator[EngineEvent]:
        step_id = new_id()
        parts = []
        visibility = BaseEngine.ABSENT
        result = BaseEngine.ABSENT
        yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id,
                          kind=self.kind, name=self.name, metadata=deepcopy(self.metadata))
        termination = BaseEngine.ABSENT
        guard = BaseEngine.ABSENT
        try:
            deadline = BaseEngine.ABSENT if timeout_seconds is BaseEngine.ABSENT else asyncio.get_running_loop().time() + timeout_seconds
            with diagnostic_context(step_id=step_id):
                operation = self.run(context)
            if inspect.isawaitable(operation):
                with diagnostic_context(step_id=step_id):
                    guard = timeout(timeout_seconds)
                    async with guard:
                        result = await operation
                if result is not BaseEngine.ABSENT and not isinstance(result, EngineOutput):
                    raise TypeError(BaseEngine.INVALID_ACTION_RESULT)
            else:
                try:
                    iterator = operation.__aiter__()
                    while True:
                        remaining = BaseEngine.ABSENT if deadline is BaseEngine.ABSENT else deadline - asyncio.get_running_loop().time()
                        if remaining is not BaseEngine.ABSENT and remaining <= BaseEngine.EXPIRED_TIMEOUT:
                            raise ExecutionLimitError(StepErrorCode.TIMEOUT, BaseEngine.TIMEOUT_MESSAGE)
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
                            if result is not BaseEngine.ABSENT:
                                raise ValueError(BaseEngine.DUPLICATE_OUTPUT)
                            result = text
                            continue
                        if result is not BaseEngine.ABSENT:
                            raise ValueError(BaseEngine.LATE_OUTPUT)
                        if isinstance(text, EngineEvent) and text.type == EngineEventType.COMPLETION:
                            if text.completion is BaseEngine.ABSENT:
                                raise ValueError(BaseEngine.MISSING_COMPLETION_RESULT)
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
                            raise TypeError(BaseEngine.INVALID_STREAM_VALUE)
                        if isinstance(text, str) and not text:
                            continue
                        event = self.delta_event(context, text, step_id=step_id)
                        if visibility is not BaseEngine.ABSENT and visibility != event.delta.visibility:
                            raise ValueError(BaseEngine.CHANGED_STREAM_VISIBILITY)
                        visibility = event.delta.visibility
                        if event.delta.operation == OutputOperation.REPLACE:
                            parts.clear()
                        parts.append(event.delta.text)
                        yield event
                except (asyncio.CancelledError, GeneratorExit) as error:
                    termination = error
                    raise
                finally:
                    close = getattr(operation, BaseEngine.CLOSE_METHOD, BaseEngine.ABSENT)
                    if close is not BaseEngine.ABSENT:
                        try:
                            await close()
                        except Exception:
                            if termination is BaseEngine.ABSENT:
                                raise
                            # 취소/GeneratorExit를 정리 오류로 바꾸지 않는다.
            # 결과 검증도 실패 이벤트/정리 범위 안에 둔다. 입력 객체는 복사 후 연결한다.
            output_context = replace(context, output_visibility=visibility or context.output_visibility)
            completed = self.step_completed_event(output_context, step_id,
                result if result is not BaseEngine.ABSENT else EngineOutput(text=OutputContract.EMPTY_TEXT.join(parts)))
            if visibility is not BaseEngine.ABSENT and visibility != completed.output.visibility:
                raise ValueError(BaseEngine.CHANGED_FINAL_VISIBILITY)
        except (asyncio.CancelledError, GeneratorExit):
            # Never yield while being cancelled/closed. RunManager finalizes the
            # persisted active Step, including cancellation before run() starts.
            raise
        except Exception as error:
            # 소유한 기한만 분류한다. action이 직접 던진 TimeoutError를 wrapper timeout으로 오인하지 않는다.
            failure = (ExecutionLimitError(StepErrorCode.TIMEOUT, BaseEngine.TIMEOUT_MESSAGE)
                       if isinstance(error, TimeoutError) and guard is not BaseEngine.ABSENT and guard.expired() else error)
            yield self.step_failed_event(step_id, failure, message=f"{self.error_message}{BaseEngine.ERROR_SEPARATOR}{failure}")
            if failure is not error:
                raise failure from error
            raise
        yield completed
        if publish_output:
            yield self.output_event(output_context, completed.output)
