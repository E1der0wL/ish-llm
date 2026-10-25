"""Engine 간에 공유하는 Tool 호출기. 실행 인자·결과·실패를 Step 이벤트로 보고한다."""

from llm.core.tool_contracts import ToolContract
from llm.core.tool_selection_records import ToolBindingRecord, ToolInvocationSelection
from llm.core.tool_usage_records import ToolUsageError, ToolUsageRecord, ToolUsageCheckpoint
from llm.core.tool_event_result import ToolEventResult
from llm.core.step_event_metadata import StepEventMetadata, StepEventPhase
from llm.core.policies import ToolPolicyValues, ToolRetryPolicy, PolicySection
from llm.core.tool_policy_binding import ToolExecutionBinding
from llm.components.tools.bindings import ToolHandlerBindings

from llm.core.checkpoint_records import CheckpointWrite, CheckpointRecords

from llm.core.models import StepKind

from llm.core.continuations import ResumeRecord

from llm.core.tool_codes import ToolErrorCode

from llm.core.models import RunErrorCode
from llm.core.results import OutputVisibility

import asyncio
import inspect
import json
import math
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Optional

from asyncio import timeout
from llm.core.checkpoints import CheckpointStatus
from llm.core.contracts import Diagnostic
from llm.errors import CodedError, stable_error_code
from llm.services.infrastructure.observability import record, ObservationKind, ObservationStatus
from llm.core.configuration import UNSET
from llm.core.models import new_id
from llm.core.json_data import JsonObject
from llm.core.results import EngineOutput
from llm.core.interactions import (
    InteractionOption,
    InteractionRequest,
    approval_request,
    same_interaction_value,
)
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.policies import ExecutionLimitError
from llm.policies.retry import RetryPolicy, RetryFailure, select_retry, change_arguments
from llm.services.runtime.operations import operation_token
from .tool_errors import ToolExecutionError, ToolApprovalRequired, ToolInvocationError
from .tool_context import ToolCallContext
from .tool_identity import ToolInvocationIdentity
from .tool_retry_records import ToolRetryCheckpoint, ToolRetryState
from .tool_approval_records import ToolApprovalRecord, ToolApprovalAction
from .operation_records import OperationClaim
from llm.core.step_tool_metadata import ToolStepMetadata
from llm.engines.context import EngineContext
from llm.engines.contracts import ResumableHandlerContract
from llm.core.models import Run


class ToolExecutionPhase:
    AUTHORIZING = "authorizing"
    EXECUTING = "executing"
    WAITING_CHILD = "waiting_child"
    RETRYING = "retrying"
    WAITING_APPROVAL = "waiting_approval"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """승인 UI/격리 실행기에 전달할 호출 스냅샷. 런타임이나 저장소를 노출하지 않는다."""

    name: str
    arguments: dict
    step_id: str
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    operation_key: Optional[str] = None
    idempotency_key: Optional[str] = None
    input_message_id: Optional[str] = None
    contract: dict = field(kw_only=True)
    classification: Optional[dict] = None

    def __post_init__(self):
        from llm.core.tool_contracts import ToolContract

        object.__setattr__(self, ToolCall.contract.__name__, ToolContract.from_dict(self.contract).to_dict())

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


def current_tool_call() -> Optional[ToolCall]:
    """실행 중인 핸들러의 출처 스냅샷. 동시 Run/분기끼리 공유하지 않으며 실행 후 해제된다.

    기본 핸들러와 같은 문맥에서 실행하는 주입 runner에 제공한다. 별도 프로세스 runner는
    전달받은 ToolCall을 자체 RPC 계약으로 운반해야 한다.
    """
    return ToolCallContext.current()


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    """Host 실행 어댑터. 실행 선택·예산·재시도·승인 임계값은 Project가 소유한다.

    authorize(call)는 정확히 True를 반환해야 실행한다. runner(tool, call)는
    별도 프로세스/호스트에 위임할 수 있는 async 함수다. 기본 실행은 OS sandbox가 아니다.
    승인 대기도 Tool timeout에 포함되며, 정책 오류를 자동 재시도하지 않는다.
    operation_key(call)는 동기 함수로 Session 범위 업무 키 또는 None을 반환한다.
    동일 키의 완료 결과만 재사용하며 불확실한 작업은 명시적인 외부 결과 확인이 필요하다.
    """

    ABSENT = None
    INVALID_REVISION = "Tool runtime revision must be nonempty"
    INVALID_SAFE_TOOLS = "retry_safe_tools must be a tuple of names"
    INVALID_ADAPTER = "Tool adapters must be callable"
    VALIDATE_TOOL = "validate_tool"

    authorize: Optional[Callable] = None
    runner: Optional[Callable] = None
    operation_key: Optional[Callable] = None
    operation_probe: Optional[Callable] = None
    # 실행 안전성은 신뢰한 개발자가 등록한다. 모델이 JSON으로 권한을 승격할 수 없다.
    retry_safe_tools: tuple[str, ...] = ()
    revision: str = "1"
    classify: Optional[Callable] = None
    # Application의 선택 메커니즘. Project의 횟수/Tool 제약을 확대할 수 없다.
    decide_retry: Optional[Callable] = None

    def __post_init__(self):
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError(ToolRuntime.INVALID_REVISION)
        if not isinstance(self.retry_safe_tools, tuple) or any(
            not isinstance(n, str) or not n for n in self.retry_safe_tools
        ):
            raise ValueError(ToolRuntime.INVALID_SAFE_TOOLS)
        if any(
            value is not ToolRuntime.ABSENT and not callable(value)
            for value in (
                self.authorize,
                self.runner,
                self.operation_key,
                self.operation_probe,
                self.classify,
                self.decide_retry,
            )
        ):
            raise TypeError(ToolRuntime.INVALID_ADAPTER)


class ToolExecutionScope:
    """한 Run의 Graph 분기·Agent가 공유하는 호출 예산. asyncio 루프에서만 접근한다."""

    ABSENT = None
    ACTIVE = False
    REVOKED = True
    REVISION_INCREMENT = 1
    COMPLETION_INCREMENT = 1
    RESERVATION_INCREMENT = 1
    CHILD_SELECTION = "Child Tool selection widens its parent"
    CHILD_BUDGET = "Child Tool budget widens its parent"
    CLOSED = "Run Tool execution scope is closed"
    INVALID_CONTRACT = "Tool contract must be ToolContract"
    MISSING_KEY_ADAPTER = "Tool requires an operation key adapter"
    MISSING_ISOLATED_RUNNER = "Tool requires a compatible isolated runner"
    BUDGET_EXHAUSTED = "Run Tool call budget exceeded"
    NOT_ALLOWED = "Tool is not allowed: {name}"
    DENIED = "Tool authorization denied: {name}"
    MISSING_SCOPE = "Run Tool execution requires an inherited ToolExecutionScope"

    USAGE_COUNTERS = ToolUsageRecord.counter_fields()
    USAGE_COLLECTIONS = ToolUsageRecord.collection_fields()
    CHECKPOINT_FIELD = ToolUsageCheckpoint.FIELD
    INHERITED_LIMITS = (ToolPolicyValues.timeout_seconds.__name__, ToolPolicyValues.max_output_chars.__name__)

    def __init__(
        self, runtime: ToolRuntime, *, policy=ABSENT, retry=ABSENT, operations=ABSENT, parent=ABSENT
    ):
        # 외부 dict 편집으로 실행 중 승인/제약의 사본이 바뀌지 않게 한다.
        from llm.core.policies import narrow_constraints
        from llm.core.policies import normalize_policies

        policy = deepcopy({} if policy is ToolExecutionScope.ABSENT else policy)
        retry = deepcopy({} if retry is ToolExecutionScope.ABSENT else retry)
        normalize_policies({PolicySection.TOOLS: policy, PolicySection.TOOL_RETRY: retry})
        policy, retry = ToolPolicyValues(**policy), ToolRetryPolicy(**retry)
        constraints = policy.argument_constraints
        self.argument_constraints = (
            constraints
            if parent is ToolExecutionScope.ABSENT
            else narrow_constraints(parent.argument_constraints, constraints)
        )
        selected = policy.allowed_tools
        self.allowed_tools = (
            tuple(selected) if selected is not ToolExecutionScope.ABSENT else ToolExecutionScope.ABSENT
        )
        if parent is not ToolExecutionScope.ABSENT and parent.allowed_tools is not ToolExecutionScope.ABSENT:
            if self.allowed_tools is ToolExecutionScope.ABSENT:
                self.allowed_tools = parent.allowed_tools
            elif not ToolPolicyValues.permits_selection(parent.allowed_tools, self.allowed_tools):
                raise ValueError(ToolExecutionScope.CHILD_SELECTION)
        self.max_calls = policy.max_calls
        if parent is not ToolExecutionScope.ABSENT and parent.max_calls is not ToolExecutionScope.ABSENT:
            if self.max_calls is not ToolExecutionScope.ABSENT and self.max_calls > parent.max_calls:
                raise ValueError(ToolExecutionScope.CHILD_BUDGET)
            if self.max_calls is ToolExecutionScope.ABSENT:
                self.max_calls = parent.max_calls
        self.timeout_seconds = policy.timeout_seconds
        self.max_output_chars = policy.max_output_chars
        if parent is not ToolExecutionScope.ABSENT:
            for key in self.INHERITED_LIMITS:
                inherited, chosen = getattr(parent, key), getattr(self, key)
                if inherited is not ToolExecutionScope.ABSENT:
                    setattr(
                        self,
                        key,
                        inherited if chosen is ToolExecutionScope.ABSENT else min(inherited, chosen),
                    )
        self.max_retries, self.retry_delay = retry.max_retries, retry.delay_seconds
        self.runtime = runtime
        self.operations = operations
        self.parent = parent
        self.restore([self.initial_usage()])
        self.revoked = ToolExecutionScope.ACTIVE

    @classmethod
    def require(cls, context):
        """Require the admitted scope; never reconstruct policy or execution state."""
        scope = getattr(context, EngineContext.tool_scope.__name__, cls.ABSENT)
        if not isinstance(scope, cls):
            raise ToolInvocationError(cls.MISSING_SCOPE)
        return scope

    def revoke(self):
        """취소 이후 남아 있는 실행기가 새로운 Tool 효과를 시작하지 못하게 한다."""
        self.revoked = ToolExecutionScope.REVOKED
        if self.parent is not ToolExecutionScope.ABSENT:
            self.parent.revoke()

    def require_active(self):
        if self.revoked:
            raise ExecutionLimitError(ToolErrorCode.SCOPE_CLOSED.value, ToolExecutionScope.CLOSED)
        if self.parent is not ToolExecutionScope.ABSENT:
            self.parent.require_active()

    @classmethod
    def initial_usage(cls):
        """새 논리 scope의 초기 상태. 저장 기록의 누락 필드를 채우는 용도가 아니다."""
        return ToolUsageRecord.initial().to_dict()

    def checkpoint(self):
        self.revision += ToolExecutionScope.REVISION_INCREMENT
        return ToolUsageRecord(
            calls=self.calls,
            completed=self.completed,
            revision=self.revision,
            pending_approvals=list(self.pending_approvals),
            invocations=sorted(self.invocations),
        ).to_dict()

    def checkpoint_record(self, value):
        """서비스의 모든 durable record에 같은 소유자 스냅샷을 기록한다."""
        return ToolUsageCheckpoint.attach(value, self.checkpoint())

    @classmethod
    def checkpoint_usage(cls, records):
        """Engine payload를 해석하지 않고 필수 공통 사용량을 복원한다."""
        return ToolUsageCheckpoint.read_records(records)

    def restore_checkpoint(self, records):
        self.restore(self.checkpoint_usage(records))

    def binding(self):
        """실행 코드/승인 어댑터 변경 시 호스트가 revision을 올려 이전 승인의 재사용을 막는다."""
        root = self
        while root.parent is not ToolExecutionScope.ABSENT:
            root = root.parent
        return ToolExecutionBinding(
            revision=root.runtime.revision,
            policy=ToolPolicyValues(
                allowed_tools=(
                    list(self.allowed_tools)
                    if self.allowed_tools is not ToolExecutionScope.ABSENT
                    else ToolExecutionScope.ABSENT
                ),
                max_calls=self.max_calls,
                timeout_seconds=self.timeout_seconds,
                max_output_chars=self.max_output_chars,
                argument_constraints=self.argument_constraints,
            ),
            approval=root.runtime.authorize is not ToolExecutionScope.ABSENT,
            classifier=root.runtime.classify is not ToolExecutionScope.ABSENT,
            retry_selector=(
                ToolExecutionBinding.SELECTOR_PRESENT
                if root.runtime.decide_retry is not ToolExecutionScope.ABSENT
                else ToolExecutionScope.ABSENT
            ),
            isolated_runner=root.runtime.runner is not ToolExecutionScope.ABSENT,
            operation_key=root.runtime.operation_key is not ToolExecutionScope.ABSENT,
            retry_safe_tools=list(self.runtime.retry_safe_tools),
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
        ).to_dict()

    @classmethod
    def validate_usage(cls, records):
        """저장 상태만 검증한다. 권한 부여·예산 예약·외부 효과 조회를 하지 않는다."""
        ToolUsageRecord.read_many(records)

    def restore(self, records):
        """검증을 모두 마친 뒤 사용량만 복원한다. 현재 한도·승인 정책은 변경하지 않는다."""
        values = ToolUsageRecord.read_many(records)
        if values:
            latest = max(values, key=lambda item: item.order)
            for key in self.USAGE_COUNTERS:
                setattr(self, key, getattr(latest, key))
            self.pending_approvals = list(latest.pending_approvals)
            self.invocations = set(latest.invocations)

    def child(self, *, allowed_tools, max_calls=ABSENT, argument_constraints=ABSENT):
        """Agent 한도를 추가하되 부모의 승인, 실행기, 원장과 예산을 유지한다."""
        return ToolExecutionScope(
            self.runtime,
            policy=ToolPolicyValues(
                allowed_tools=list(allowed_tools),
                max_calls=max_calls,
                argument_constraints=(
                    {} if argument_constraints is ToolExecutionScope.ABSENT else argument_constraints
                ),
            ).to_dict(),
            retry=ToolRetryPolicy(max_retries=self.max_retries, delay_seconds=self.retry_delay).to_dict(),
            operations=self.operations,
            parent=self,
        )

    def validate(self, tool):
        """모델/효과 호출 전에 호스트 실행 계약을 검사한다."""
        self.require_active()
        from llm.components.tools.constraints import constrained_parameters

        constrained_parameters(tool.parameters, self.argument_constraints.get(tool.name, {}))
        contract = tool.contract
        from llm.core.tool_contracts import ToolContract, ToolIsolation

        if not isinstance(contract, ToolContract):
            raise TypeError(ToolExecutionScope.INVALID_CONTRACT)
        root = self
        while root.parent is not ToolExecutionScope.ABSENT:
            root = root.parent
        if contract.operation_key_required and self.runtime.operation_key is ToolExecutionScope.ABSENT:
            raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, ToolExecutionScope.MISSING_KEY_ADAPTER)
        if contract.isolation != ToolIsolation.NONE:
            validate = getattr(self.runtime.runner, ToolRuntime.VALIDATE_TOOL, ToolExecutionScope.ABSENT)
            if validate is ToolExecutionScope.ABSENT:
                raise ExecutionLimitError(
                    RunErrorCode.TOOL_CONTRACT, ToolExecutionScope.MISSING_ISOLATED_RUNNER
                )
            validate(tool.name, contract.isolation)

    def record_completion(self):
        self.completed += ToolExecutionScope.COMPLETION_INCREMENT
        if self.parent is not ToolExecutionScope.ABSENT:
            self.parent.record_completion()

    async def authorize(self, call: ToolCall, *, decision=ABSENT, invocation=ABSENT):
        self.require_active()
        if self.allowed_tools is not ToolExecutionScope.ABSENT and call.name not in self.allowed_tools:
            raise ExecutionLimitError(
                RunErrorCode.TOOL_DENIED, ToolExecutionScope.NOT_ALLOWED.format(name=call.name)
            )
        identity = ToolInvocationIdentity.pending(call.name, call.arguments)
        pending = decision is not ToolExecutionScope.ABSENT and identity in self.pending_approvals
        reserved = pending or invocation is not ToolExecutionScope.ABSENT and invocation in self.invocations
        if not reserved and self.max_calls is not ToolExecutionScope.ABSENT and self.calls >= self.max_calls:
            raise ExecutionLimitError(RunErrorCode.TOOL_BUDGET_EXCEEDED, ToolExecutionScope.BUDGET_EXHAUSTED)
        # await 이전에 예약하므로 동시에 진행되는 Graph 분기도 한도를 공유한다.
        if pending:
            self.pending_approvals.remove(identity)
        if not reserved:
            self.calls += ToolExecutionScope.RESERVATION_INCREMENT
        if invocation is not ToolExecutionScope.ABSENT:
            self.invocations.add(invocation)
        try:
            if self.parent is not ToolExecutionScope.ABSENT:
                await self.parent.authorize(call, decision=decision, invocation=invocation)
            if decision is ToolApprovalRequired.DENIED:
                raise ExecutionLimitError(
                    RunErrorCode.TOOL_DENIED, ToolExecutionScope.DENIED.format(name=call.name)
                )
            if self.parent is ToolExecutionScope.ABSENT:
                # 기술적인 거부는 저장된 승인으로도 우회할 수 없다. ASK만 기존 결정을 소비한다.
                if self.runtime.authorize is not ToolExecutionScope.ABSENT:
                    try:
                        allowed = await self.runtime.authorize(deepcopy(call))
                    except ToolApprovalRequired:
                        if decision is not ToolApprovalRequired.ALLOWED:
                            raise
                    else:
                        if allowed is not ToolApprovalRequired.ALLOWED:
                            raise ExecutionLimitError(
                                RunErrorCode.TOOL_DENIED, ToolExecutionScope.DENIED.format(name=call.name)
                            )
                if (
                    decision is not ToolApprovalRequired.ALLOWED
                    and ToolContract.from_dict(call.contract).approval_required
                ):
                    raise ToolApprovalRequired()
        except ToolApprovalRequired:
            self.pending_approvals.append(identity)
            raise


class ToolExecutor:
    """Tool 핸들러 실행을 하나의 Step으로 표현한다. 저장은 기존 StepManager가 담당한다."""

    ABSENT = None
    UNBOUND_STEP = ""
    MINIMUM_TIMEOUT = 0
    MINIMUM_OUTPUT_CHARS = 1
    EXPIRED_TIMEOUT = 0
    UNAUTHORIZED = False
    AUTHORIZED = True
    DURABLE_APPROVAL = True
    UNRELATED_CONTINUATION = False
    CONFIRMED_NO_EFFECT = True
    JSON_ENSURE_ASCII = False
    JSON_ALLOW_NAN = False
    RESULT = "result"
    INVALID_TIMEOUT = "Tool timeout must be positive and finite"
    INVALID_OUTPUT_LIMIT = "Tool output limit must be a positive integer"
    INVALID_CLASSIFIER = "Trusted classifier must return ToolClassification"
    MISSING_OPERATION_KEY = "Tool operation key cannot be empty"
    MISSING_OPERATIONS = "Operation keys require a Session-bound operation service"
    NOT_TOOL_APPROVAL = "Checkpoint decision is not a durable Tool approval"
    CHANGED_CLASSIFICATION = "Durable Tool approval classification changed"
    CHANGED_APPROVAL_POLICY = "Durable Tool approval policy/adapter binding changed"
    CHANGED_CONTRACT = "Durable Tool approval Tool contract changed"
    CHANGED_CONSTRAINTS = "Durable Tool approval argument constraints changed"
    MISMATCHED_APPROVAL = "Durable Tool approval does not match its checkpoint invocation"
    INVALID_APPROVAL_DECISION = "Durable Tool approval has an invalid interaction decision"
    MISSING_APPROVAL_KEY = "Durable Tool approval resume requires its stable request_key"
    OUTPUT_EXCEEDED = "Tool output limit exceeded"
    LATE_APPROVAL = "Approval requests must originate before Tool execution"
    TIMED_OUT = "Tool execution or authorization timed out"
    FAILED = "Tool execution failed: {error}"
    CONTAINER_KEY = "An orchestration Tool uses child operation receipts, not a container operation key"
    UNCHANGED_REPAIR = "Recoverable Tool failure requires different arguments"
    FAILED_NO_EFFECT_RECEIPT = "Could not persist the no-effect Tool receipt"
    RETRY_EXCEEDED = "Tool retry checkpoint exceeds the configured retry limit"
    MISSING_BINDING = "Selected Tool execution binding requires a durable checkpoint"
    CHANGED_BINDING = "Selected Tool execution configuration changed"

    def __init__(self, *, timeout_seconds=ABSENT, max_output_chars=ABSENT):
        if timeout_seconds is not ToolExecutor.ABSENT and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= ToolExecutor.MINIMUM_TIMEOUT
        ):
            raise ValueError(ToolExecutor.INVALID_TIMEOUT)
        if max_output_chars is not ToolExecutor.ABSENT and (
            type(max_output_chars) is not int or max_output_chars < ToolExecutor.MINIMUM_OUTPUT_CHARS
        ):
            raise ValueError(ToolExecutor.INVALID_OUTPUT_LIMIT)
        self.timeout_seconds, self.max_output_chars = timeout_seconds, max_output_chars

    async def _classify(self, tool, arguments, context, scope):
        from llm.core.tool_contracts import ToolClassification

        classification = tool.classification or ToolClassification()
        if scope.runtime.classify is not ToolExecutor.ABSENT:
            classification = scope.runtime.classify(
                ToolCall(
                    tool.name,
                    deepcopy(arguments),
                    ToolExecutor.UNBOUND_STEP,
                    context.project.id,
                    context.session.id,
                    context.run.id,
                    contract=tool.contract.to_dict(),
                )
            )
            if inspect.isawaitable(classification):
                classification = await classification
        if not isinstance(classification, ToolClassification):
            raise TypeError(ToolExecutor.INVALID_CLASSIFIER)
        return asdict(classification)

    def _retry_state(self, context, request_key, arguments):
        """복구 상태는 기존 Engine checkpoint의 보조 record이며 별도 저장소를 만들지 않는다."""
        key = ToolRetryCheckpoint.key(request_key)
        saved = CheckpointRecords.records_or_empty(
            getattr(context, EngineContext.checkpoint.__name__, ToolExecutor.ABSENT)
        ).get(key, {})
        return ToolRetryCheckpoint.read(saved, arguments)

    def _retry_event(self, state, metadata, request_key):
        name = StepEventMetadata.checkpoint_name(metadata)
        if name is ToolExecutor.ABSENT or request_key is ToolExecutor.ABSENT:
            return ToolExecutor.ABSENT
        return EngineEvent(
            EngineEventType.CHECKPOINT,
            metadata=CheckpointWrite.record_event(
                name, ToolRetryCheckpoint.event_key(request_key), ToolRetryCheckpoint.create(state)
            ).to_dict(),
        )

    def _operation_call(self, call, scope, tool):
        """수정 인자의 business key도 원래 공개 계약으로 재계산한다. 충돌은 원장이 거부한다."""
        call = replace(call, operation_key=ToolExecutor.ABSENT, idempotency_key=ToolExecutor.ABSENT)
        if scope.runtime.operation_key is not ToolExecutor.ABSENT:
            key = scope.runtime.operation_key(deepcopy(call))
            if key is not ToolExecutor.ABSENT:
                if scope.operations is ToolExecutor.ABSENT:
                    raise ValueError(ToolExecutor.MISSING_OPERATIONS)
                call = replace(
                    call,
                    operation_key=key,
                    idempotency_key=operation_token(call.project_id, call.session_id, key),
                )
        if tool.contract.operation_key_required and call.operation_key is ToolExecutor.ABSENT:
            raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, ToolExecutor.MISSING_OPERATION_KEY)
        return call

    def _request_state(self, tool, arguments, context, decision, request_key, classification, scope):
        """권한을 부여하지 않고 기존 체크포인트 연결만 검증한다. 계측 여부와 무관하다."""
        records = CheckpointRecords.records_or_empty(
            getattr(context, EngineContext.checkpoint.__name__, ToolExecutor.ABSENT)
        )
        run = getattr(context, EngineContext.run.__name__, ToolExecutor.ABSENT)
        decisions = ResumeRecord.decisions_for(getattr(run, Run.metadata.__name__, {}))
        previous = ToolApprovalRecord(records.get(request_key, {}))
        # This predicate stays shallow for unrelated Workflow continuation records.
        durable_approval = previous.is_approval
        binding = previous.binding
        stored_decision = decisions.get(request_key)
        if durable_approval and request_key in decisions:
            try:
                stored_decision = previous.select(stored_decision)
            except (ValueError, TypeError, KeyError) as error:
                raise ToolInvocationError(ToolExecutor.INVALID_APPROVAL_DECISION) from error
        if decision is UNSET:
            decision = (
                stored_decision if durable_approval and request_key in decisions else ToolExecutor.ABSENT
            )

        if durable_approval:
            action = previous.action
            if not same_interaction_value(action.classification, classification):
                raise ToolInvocationError(ToolExecutor.CHANGED_CLASSIFICATION)
            if not same_interaction_value(
                action.policy, scope.binding()
            ):
                raise ToolInvocationError(ToolExecutor.CHANGED_APPROVAL_POLICY)
            if not same_interaction_value(action.contract, tool.contract.to_dict()):
                raise ToolInvocationError(ToolExecutor.CHANGED_CONTRACT)
            if not same_interaction_value(
                ToolExecutionBinding.constraints(action.policy_or_empty), scope.argument_constraints
            ):
                raise ToolInvocationError(ToolExecutor.CHANGED_CONSTRAINTS)
            if (
                request_key not in decisions
                or type(stored_decision) is not bool
                or type(decision) is not bool
                or decision != stored_decision
                or binding.key != request_key
                or action.tool != tool.name
                or not same_interaction_value(action.arguments, arguments)
            ):
                raise ToolInvocationError(ToolExecutor.MISMATCHED_APPROVAL)
            return decision, ToolExecutor.DURABLE_APPROVAL

        if request_key in records and decision is not ToolExecutor.ABSENT:
            raise ToolInvocationError(ToolExecutor.NOT_TOOL_APPROVAL)

        if decision is not ToolExecutor.ABSENT and (
            request_key is ToolExecutor.ABSENT or request_key not in records
        ):
            # Stable keys detect a missing binding; never guess approval or dedup identity.
            for key, value in records.items():
                candidate = ToolApprovalRecord(value)
                action = candidate.action
                if (
                    key in decisions
                    and candidate.is_approval
                    and action.tool == tool.name
                    and same_interaction_value(action.arguments, arguments)
                ):
                    raise ToolInvocationError(ToolExecutor.MISSING_APPROVAL_KEY)
        identity = ToolInvocationIdentity.pending(tool.name, arguments)
        resumed = (
            decision is not ToolExecutor.ABSENT
            and identity in scope.pending_approvals
        )
        return decision, ToolExecutor.UNRELATED_CONTINUATION if request_key in records else resumed

    async def _execute(
        self,
        tool,
        arguments,
        *,
        result,
        scope,
        metadata=ABSENT,
        context=ABSENT,
        decision=ABSENT,
        classification=ABSENT,
        request_key=ABSENT,
        retry_state=ABSENT,
    ):
        JsonObject.validate(arguments)
        step_id = new_id()
        scope.validate(tool)
        call = ToolCall(
            tool.name,
            deepcopy(arguments),
            step_id,
            context.project.id if context else ToolExecutor.ABSENT,
            context.session.id if context else ToolExecutor.ABSENT,
            context.run.id if context else ToolExecutor.ABSENT,
            input_message_id=context.run.input_message_id if context else ToolExecutor.ABSENT,
            contract=tool.contract.to_dict(),
            classification=classification,
        )

        # Project의 제한은 모든 엔진이 공유하며 로컬 호출은 좁히기만 한다.
        def narrow(left, right):
            return (
                min(v for v in (left, right) if v is not ToolExecutor.ABSENT)
                if left is not ToolExecutor.ABSENT or right is not ToolExecutor.ABSENT
                else ToolExecutor.ABSENT
            )

        seconds = narrow(self.timeout_seconds, scope.timeout_seconds)
        output_limit = narrow(self.max_output_chars, scope.max_output_chars)
        call = self._operation_call(call, scope, tool)
        if scope.max_retries is not ToolExecutor.ABSENT:
            event = self._retry_event(retry_state, metadata, request_key)
            if event is not ToolExecutor.ABSENT:
                yield event
        yield EngineEvent(
            EngineEventType.STEP_STARTED,
            step_id=step_id,
            kind=StepKind.TOOL,
            name=tool.name,
            metadata=ToolStepMetadata.started(metadata, arguments, ToolExecutionPhase.AUTHORIZING, call),
        )
        authorized = ToolExecutor.UNAUTHORIZED
        try:
            deadline = RetryPolicy.end(seconds)
            retry = RetryPolicy(
                (
                    ToolExecutor.ABSENT
                    if scope.max_retries is ToolExecutor.ABSENT
                    else scope.max_retries + ToolRetryState.ATTEMPT_INCREMENT
                ),
                scope.retry_delay,
                deadline=deadline,
            )
            async with timeout(seconds):
                invocation_key = retry_state.invocation_key(request_key)
                invocation = (
                    ToolInvocationIdentity.invocation(
                        getattr(context, EngineContext.checkpoint_scope.__name__, ToolExecutor.ABSENT),
                        invocation_key,
                    )
                    if request_key is not ToolExecutor.ABSENT
                    else ToolExecutor.ABSENT
                )
                await scope.authorize(call, decision=decision, invocation=invocation)
            authorized = ToolExecutor.AUTHORIZED
            # yield 바깥 서비스 I/O까지 timeout context가 취소하지 않도록 분리한다.
            yield EngineEvent(
                EngineEventType.STEP_UPDATED,
                step_id=step_id,
                metadata=ToolStepMetadata.authorized(ToolExecutionPhase.EXECUTING),
            )
            remaining = (
                ToolExecutor.ABSENT
                if deadline is ToolExecutor.ABSENT
                else deadline - asyncio.get_running_loop().time()
            )
            if remaining is not ToolExecutor.ABSENT and remaining <= ToolExecutor.EXPIRED_TIMEOUT:
                raise asyncio.TimeoutError()
            async with timeout(remaining):
                reservation = OperationClaim(
                    await scope.operations.claim(call)
                    if call.operation_key is not ToolExecutor.ABSENT
                    else OperationClaim.started()
                )
            value = reservation.result
            if reservation.reused:
                record(ObservationKind.TOOLS.value, ObservationStatus.REUSED.value, name=tool.name)
            if not reservation.reused:
                attempt = retry_state.attempt
                uncertain_attempt = retry_state.uncertain
                while not reservation.reused:
                    remaining = (
                        ToolExecutor.ABSENT
                        if deadline is ToolExecutor.ABSENT
                        else deadline - asyncio.get_running_loop().time()
                    )
                    if remaining is not ToolExecutor.ABSENT and remaining <= ToolExecutor.EXPIRED_TIMEOUT:
                        raise asyncio.TimeoutError()
                    try:
                        scope.require_active()
                        retry_state.begin()
                        if scope.max_retries is not ToolExecutor.ABSENT:
                            event = self._retry_event(retry_state, metadata, request_key)
                            if event is not ToolExecutor.ABSENT:
                                yield event
                        record(
                            ObservationKind.TOOLS.value,
                            ObservationStatus.EXECUTIONS.value,
                            retry=attempt > ToolRetryState.INITIAL_ATTEMPT,
                            name=tool.name,
                            run_id=call.run_id,
                            step_id=call.step_id,
                        )
                        stream = ToolHandlerBindings.events(tool.handler)
                        if ResumableHandlerContract.declared(tool.handler) and callable(stream):
                            if call.operation_key is not ToolExecutor.ABSENT:
                                raise ValueError(ToolExecutor.CONTAINER_KEY)
                            from contextlib import aclosing

                            nested = {}
                            async with aclosing(
                                stream(
                                    context,
                                    deepcopy(arguments),
                                    request_key=request_key,
                                    checkpoint_name=StepEventMetadata.checkpoint_name(metadata),
                                    step_id=step_id,
                                    result=nested,
                                )
                            ) as events:
                                while True:
                                    try:
                                        left = (
                                            ToolExecutor.ABSENT
                                            if deadline is ToolExecutor.ABSENT
                                            else deadline - asyncio.get_running_loop().time()
                                        )
                                        # 저장 소비자를 취소하지 않고 다음 자식 이벤트 대기만 제한한다.
                                        async with timeout(left):
                                            event = await anext(events)
                                    except StopAsyncIteration:
                                        break
                                    if event.type == EngineEventType.PAUSED:
                                        yield EngineEvent(
                                            EngineEventType.STEP_CANCELLED,
                                            step_id=step_id,
                                            metadata=StepEventMetadata.phase(
                                                ToolExecutionPhase.WAITING_CHILD
                                            ),
                                        )
                                        ToolEventResult(result).pause()
                                        yield event
                                        return
                                    yield event
                            value = ToolEventResult(nested).value
                        else:
                            async with timeout(remaining):
                                with ToolCallContext.scope(call):
                                    if scope.runtime.runner is ToolExecutor.ABSENT:
                                        value = await tool.handler(deepcopy(arguments))
                                    else:
                                        value = await scope.runtime.runner(tool, deepcopy(call))
                        break
                    except Exception as error:
                        uncertain_attempt = uncertain_attempt or not (
                            isinstance(error, ToolExecutionError)
                            and error.effect == ToolExecutionError.NO_EFFECT
                        )
                        if (
                            isinstance(error, ToolExecutionError)
                            and error.effect == ToolExecutionError.NO_EFFECT
                        ):
                            safe = ToolExecutor.CONFIRMED_NO_EFFECT
                        else:
                            safe = tool.name in scope.runtime.retry_safe_tools
                        retryable = isinstance(error, ToolExecutionError) and error.retryable
                        repairable = (
                            isinstance(error, ToolExecutionError)
                            and error.recoverable
                            and not uncertain_attempt
                            and bool(tool.contract.retry_fields)
                        )
                        if (
                            isinstance(error, ToolExecutionError)
                            and error.effect == ToolExecutionError.NO_EFFECT
                            and not uncertain_attempt
                            and call.operation_key is not ToolExecutor.ABSENT
                        ):
                            try:
                                await scope.operations.not_applied(call, str(error))
                            except Exception as persistence_error:
                                raise RuntimeError(
                                    ToolExecutor.FAILED_NO_EFFECT_RECEIPT
                                ) from persistence_error
                        retry_state.failed(error, uncertain_attempt, arguments)
                        if scope.max_retries is not ToolExecutor.ABSENT:
                            event = self._retry_event(retry_state, metadata, request_key)
                            if event is not ToolExecutor.ABSENT:
                                yield event
                        if (
                            not safe
                            or not (retryable or repairable)
                            or not retry.allows(attempt + ToolRetryState.ATTEMPT_INCREMENT)
                        ):
                            if (
                                isinstance(error, ToolExecutionError)
                                and error.effect == ToolExecutionError.NO_EFFECT
                                and uncertain_attempt
                            ):
                                raise ToolExecutionError(
                                    str(error), effect=ToolExecutionError.UNCERTAIN_EFFECT
                                ) from error
                            raise
                        fields = tool.contract.retry_fields if repairable else ()
                        async with timeout(retry.remaining()):
                            selected = await select_retry(
                                RetryFailure(
                                    tool.name,
                                    deepcopy(arguments),
                                    error.code,
                                    str(error),
                                    attempt + ToolRetryState.ATTEMPT_INCREMENT,
                                    retryable,
                                    fields,
                                ),
                                scope.runtime.decide_retry,
                            )
                        if selected is ToolExecutor.ABSENT:
                            raise
                        from llm.components.tools.constraints import constrained_arguments

                        def validate(candidate):
                            JsonObject.validate(candidate)
                            constrained_arguments(
                                tool.parameters, candidate, scope.argument_constraints.get(tool.name, {})
                            )

                        candidate = change_arguments(arguments, selected, allowed=fields, validate=validate)
                        modified = not same_interaction_value(candidate, arguments)
                        if not modified and not retryable:
                            raise ValueError(ToolExecutor.UNCHANGED_REPAIR) from error
                        attempt += ToolRetryState.ATTEMPT_INCREMENT
                        retry_state.selected(
                            error, attempt, arguments, candidate, modified, uncertain_attempt
                        )
                        yield EngineEvent(
                            EngineEventType.STEP_UPDATED,
                            step_id=step_id,
                            metadata=ToolStepMetadata.retrying(
                                ToolExecutionPhase.RETRYING,
                                attempt,
                                error.effect,
                                str(error),
                                retry_state.failures,
                            ),
                        )
                        event = self._retry_event(retry_state, metadata, request_key)
                        if event is not ToolExecutor.ABSENT:
                            yield event
                        await retry.wait(attempt)
                        if modified:
                            arguments = candidate
                            async with timeout(retry.remaining()):
                                classification = await self._classify(tool, arguments, context, scope)
                            call = self._operation_call(
                                replace(call, arguments=deepcopy(arguments), classification=classification),
                                scope,
                                tool,
                            )
                            # 이전 인자의 approval은 새 invocation에 권한을 주지 않는다.
                            authorized = ToolExecutor.UNAUTHORIZED
                            invocation = (
                                ToolInvocationIdentity.invocation(
                                    getattr(
                                        context, EngineContext.checkpoint_scope.__name__, ToolExecutor.ABSENT
                                    ),
                                    retry_state.repair_key(request_key),
                                )
                                if request_key is not ToolExecutor.ABSENT
                                else ToolExecutor.ABSENT
                            )
                            async with timeout(retry.remaining()):
                                await scope.authorize(
                                    call, decision=ToolExecutor.ABSENT, invocation=invocation
                                )
                            authorized = ToolExecutor.AUTHORIZED
                            yield EngineEvent(
                                EngineEventType.STEP_UPDATED,
                                step_id=step_id,
                                metadata=ToolStepMetadata.repaired(
                                    ToolExecutionPhase.EXECUTING, arguments, call
                                ),
                            )
                        if call.operation_key is not ToolExecutor.ABSENT and not uncertain_attempt:
                            async with timeout(retry.remaining()):
                                reservation = OperationClaim(await scope.operations.claim(call))
                            if reservation.reused:
                                value = reservation.required_result
                                record(
                                    ObservationKind.TOOLS.value,
                                    ObservationStatus.REUSED.value,
                                    name=tool.name,
                                )
            JsonObject.validate({ToolExecutor.RESULT: value})
            content = (
                value
                if isinstance(value, str)
                else json.dumps(
                    value, ensure_ascii=ToolExecutor.JSON_ENSURE_ASCII, allow_nan=ToolExecutor.JSON_ALLOW_NAN
                )
            )
            if output_limit is not ToolExecutor.ABSENT and len(content) > output_limit:
                raise ValueError(ToolExecutor.OUTPUT_EXCEEDED)
            if call.operation_key is not ToolExecutor.ABSENT and not reservation.reused:
                await scope.operations.complete(call, value)
            ToolEventResult(result).complete(value, content)
            if retry_state.attempt:
                ToolEventResult(result).record_retry(retry_state.to_dict())
            scope.record_completion()
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except ToolApprovalRequired as request:
            if authorized:
                error = ToolExecutionError(
                    ToolExecutor.LATE_APPROVAL, effect=ToolExecutionError.UNCERTAIN_EFFECT
                )
                yield BaseEngine.step_failed_event(
                    step_id,
                    error,
                    code=ToolExecutionError.code,
                    metadata=StepEventMetadata.failure(StepEventPhase.FAILED.value, ToolExecutionError.code),
                )
                raise error from request
            # 표시용 문구와 실제 실행 대상은 분리한다. 호출자 제공 source/action은 신뢰하지 않는다.
            request.request = ToolApprovalAction.bind(request.request, call, scope.binding())
            yield EngineEvent(
                EngineEventType.STEP_CANCELLED,
                step_id=step_id,
                metadata=ToolStepMetadata.waiting(ToolExecutionPhase.WAITING_APPROVAL, arguments),
            )
            raise
        except Exception as error:
            if isinstance(error, asyncio.TimeoutError):
                error = ExecutionLimitError(RunErrorCode.TOOL_TIMEOUT, ToolExecutor.TIMED_OUT)
            yield BaseEngine.step_failed_event(
                step_id,
                error,
                code=ToolExecutionError.code,
                message=ToolExecutor.FAILED.format(error=error),
                metadata=StepEventMetadata.failure(
                    StepEventPhase.FAILED.value,
                    getattr(error, Diagnostic.code.__name__, ToolExecutionError.code),
                ),
            )
            raise error
        yield EngineEvent(
            EngineEventType.STEP_COMPLETED,
            step_id=step_id,
            output=EngineOutput(step_id, data=value, step_id=step_id, visibility=OutputVisibility.INTERNAL),
            metadata=ToolStepMetadata.completed(StepEventPhase.COMPLETED.value, reservation.reused),
        )

    async def execute(
        self, tool, arguments, *, result, metadata=ABSENT, context=ABSENT, decision=UNSET, request_key=ABSENT
    ):
        """논리 호출 계측은 이 경계 하나에 둔다. 재개 여부는 기존 승인 예약에서 읽는다."""
        from contextlib import aclosing
        from llm.components.tools.constraints import constrained_arguments

        # A context-free call is an independent invocation, not a replacement
        # for a missing Run scope. Every Run-bound caller must preserve its owner.
        scope = (ToolExecutionScope(ToolRuntime()) if context is ToolExecutor.ABSENT
                 else ToolExecutionScope.require(context))
        arguments = constrained_arguments(
            tool.parameters,
            arguments,
            scope.argument_constraints.get(tool.name, {}),
        )
        retry_state = self._retry_state(context, request_key, arguments)
        arguments = retry_state.arguments
        if retry_state.attempt and (
            scope.max_retries is ToolExecutor.ABSENT
            or retry_state.attempt > scope.max_retries
        ):
            raise ToolInvocationError(ToolExecutor.RETRY_EXCEEDED)
        arguments = constrained_arguments(
            tool.parameters,
            arguments,
            scope.argument_constraints.get(tool.name, {}),
        )
        classification = await self._classify(tool, arguments, context, scope)
        bind = ToolHandlerBindings.declared_binder(tool.handler)
        if callable(bind):
            selected = ToolInvocationSelection(tool.name, arguments, bind(deepcopy(arguments), context))
            name = StepEventMetadata.checkpoint_name(metadata)
            if context is ToolExecutor.ABSENT or not request_key or not name:
                raise ToolInvocationError(ToolExecutor.MISSING_BINDING)
            key = ToolBindingRecord.key_for(request_key)
            saved = CheckpointRecords.records_or_empty(context.checkpoint).get(key)
            if saved is not ToolExecutor.ABSENT:
                saved = context.read_tool_binding(saved)
                saved.require_key(key)
                saved.require_checkpoint(name)
                if saved.selection != selected:
                    raise ToolInvocationError(ToolExecutor.CHANGED_BINDING)
            # Selection is durable before ASK, authorization, or any Tool effect.
            yield EngineEvent(
                EngineEventType.CHECKPOINT,
                metadata=CheckpointWrite.record_event(
                    name, key, ToolBindingRecord.create(selected, request_key, name).to_dict()
                ).to_dict(),
            )
        decision, resumed = self._request_state(
            tool, arguments, context, decision, request_key, classification, scope
        )
        if request_key is not ToolExecutor.ABSENT:
            invocation = ToolInvocationIdentity.invocation(
                getattr(context, EngineContext.checkpoint_scope.__name__, ToolExecutor.ABSENT), request_key
            )
            resumed = resumed or invocation in scope.invocations
        if not resumed:
            record(ObservationKind.TOOLS.value, ObservationStatus.REQUESTS.value, name=tool.name)
        started = time.monotonic()
        status, code = ObservationStatus.COMPLETED.value, ToolExecutor.ABSENT
        try:
            async with aclosing(
                self._execute(
                    tool,
                    arguments,
                    result=result,
                    scope=scope,
                    metadata=metadata,
                    context=context,
                    decision=decision,
                    classification=classification,
                    request_key=request_key,
                    retry_state=retry_state,
                )
            ) as events:
                async for event in events:
                    if event.type == EngineEventType.PAUSED:
                        status = ObservationStatus.PAUSED.value
                    yield event
            if ToolEventResult(result).paused:
                status = ObservationStatus.PAUSED.value
        except ToolApprovalRequired:
            status = ObservationStatus.APPROVAL_REQUIRED.value
            raise
        except (asyncio.CancelledError, GeneratorExit):
            if not ToolEventResult(result).paused:
                status = ObservationStatus.CANCELLED.value
            raise
        except Exception as error:
            status, code = ObservationStatus.FAILED.value, stable_error_code(error) or ToolExecutionError.code
            raise
        finally:
            record(
                ObservationKind.TOOLS.value,
                status,
                code=code,
                name=tool.name,
                duration_seconds=time.monotonic() - started,
            )
