"""Engine 간에 공유하는 Tool 호출기. 실행 인자·결과·실패를 Step 이벤트로 보고한다."""

import asyncio
import inspect
import json
import math
import time
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Optional

from asyncio import timeout
from llm.core.contracts import Diagnostic
from llm.errors import CodedError, stable_error_code
from llm.services.infrastructure.observability import record
from llm.core.configuration import UNSET
from llm.core.models import ProjectConfig, new_id
from llm.core.results import EngineOutput
from llm.core.interactions import InteractionRequest, approval_request, same_interaction_value
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.policies import ExecutionLimitError
from llm.services.runtime.operations import operation_token


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
    contract: Optional[dict] = None
    classification: Optional[dict] = None


_tool_call = ContextVar("llm_tool_call", default=None)


class ToolExecutionError(CodedError, RuntimeError):
    """핸들러가 보장하는 실패 분류. 효과가 없다는 증거가 있을 때만 effect='none'을 사용한다."""

    code = "tool_failed"

    def __init__(self, message: str, *, effect="uncertain", retryable=False):
        if effect not in ("none", "uncertain") or type(retryable) is not bool:
            raise ValueError("Invalid Tool failure classification")
        super().__init__(message)
        self.effect, self.retryable = effect, retryable

    @property
    def diagnostic(self):
        return Diagnostic("tool_failed", str(self), details={"effect": self.effect, "retryable": self.retryable})


class ToolApprovalRequired(RuntimeError):
    """authorize가 발생시키면 공통 요청을 체크포인트에 저장하고 Run을 일시정지한다."""

    def __init__(self, message="Tool 실행 승인", *, request: Optional[InteractionRequest] = None):
        if request is not None and not isinstance(request, InteractionRequest):
            raise TypeError("request must be an InteractionRequest")
        self.request = InteractionRequest.from_dict(request.to_dict()) if request else approval_request(str(message))
        if self.request.kind != "approval" or self.request.input_schema is not None or any(
                type(o.value) is not bool or o.effect != ("approve" if o.value else "deny")
                for o in self.request.options):
            raise ValueError("Tool approval options must be boolean and cannot modify arguments")
        super().__init__(self.request.title)



def current_tool_call() -> Optional[ToolCall]:
    """실행 중인 핸들러의 출처 스냅샷. 동시 Run/분기끼리 공유하지 않으며 실행 후 해제된다.

    기본 핸들러와 같은 문맥에서 실행하는 주입 runner에 제공한다. 별도 프로세스 runner는
    전달받은 ToolCall을 자체 RPC 계약으로 운반해야 한다.
    """
    return deepcopy(_tool_call.get())


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    """Host 실행 어댑터. 실행 선택·예산·재시도·승인 임계값은 Project가 소유한다.

    authorize(call)는 정확히 True를 반환해야 실행한다. runner(tool, call)는
    별도 프로세스/호스트에 위임할 수 있는 async 함수다. 기본 실행은 OS sandbox가 아니다.
    승인 대기도 Tool timeout에 포함되며, 정책 오류를 자동 재시도하지 않는다.
    operation_key(call)는 동기 함수로 Session 범위 업무 키 또는 None을 반환한다.
    동일 키의 완료 결과만 재사용하며 불확실한 작업은 명시적인 외부 결과 확인이 필요하다.
    """

    authorize: Optional[Callable] = None
    runner: Optional[Callable] = None
    operation_key: Optional[Callable] = None
    operation_probe: Optional[Callable] = None
    # 실행 안전성은 신뢰한 개발자가 등록한다. 모델이 JSON으로 권한을 승격할 수 없다.
    retry_safe_tools: tuple[str, ...] = ()
    revision: str = "1"
    classify: Optional[Callable] = None

    def __post_init__(self):
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError("Tool runtime revision must be nonempty")
        if not isinstance(self.retry_safe_tools, tuple) or any(not isinstance(n, str) or not n for n in self.retry_safe_tools):
            raise ValueError("retry_safe_tools must be a tuple of names")
        if any(value is not None and not callable(value) for value in (
                self.authorize, self.runner, self.operation_key, self.operation_probe, self.classify)):
            raise TypeError("Tool adapters must be callable")


class ToolExecutionScope:
    """한 Run의 Graph 분기·Agent가 공유하는 호출 예산. asyncio 루프에서만 접근한다."""

    def __init__(self, runtime: ToolRuntime, *, policy=None, retry=None, operations=None, parent=None):
        # 외부 dict 편집으로 실행 중 승인/제약의 사본이 바뀌지 않게 한다.
        from llm.components.tools.constraints import narrow_constraints
        from llm.core.policies import normalize_policies
        policy, retry = deepcopy(policy or {}), deepcopy(retry or {})
        normalize_policies({"tools": policy, "tool_retry": retry})
        constraints = policy.get("argument_constraints", {})
        self.argument_constraints = (constraints if parent is None else
                                     narrow_constraints(parent.argument_constraints, constraints))
        selected = policy.get("allowed_tools")
        self.allowed_tools = tuple(selected) if selected is not None else None
        if parent is not None and parent.allowed_tools is not None:
            if self.allowed_tools is None:
                self.allowed_tools = parent.allowed_tools
            elif not set(self.allowed_tools).issubset(parent.allowed_tools):
                raise ValueError("Child Tool selection widens its parent")
        self.max_calls = policy.get("max_calls")
        if parent is not None and parent.max_calls is not None:
            if self.max_calls is not None and self.max_calls > parent.max_calls:
                raise ValueError("Child Tool budget widens its parent")
            if self.max_calls is None:
                self.max_calls = parent.max_calls
        self.timeout_seconds = policy.get("timeout_seconds")
        self.max_output_chars = policy.get("max_output_chars")
        if parent is not None:
            for key in ("timeout_seconds", "max_output_chars"):
                inherited, chosen = getattr(parent, key), getattr(self, key)
                if inherited is not None:
                    setattr(self, key, inherited if chosen is None else min(inherited, chosen))
        self.max_retries, self.retry_delay = retry.get("max_retries"), retry.get("delay_seconds")
        self.runtime = runtime
        self.calls = 0
        self.operations = operations
        self.parent = parent
        self.completed = 0
        self.pending_approvals = []
        self.invocations = set()
        self.revision = 0
        self.revoked = False

    def revoke(self):
        """취소 이후 남아 있는 실행기가 새로운 Tool 효과를 시작하지 못하게 한다."""
        self.revoked = True
        if self.parent is not None:
            self.parent.revoke()

    def require_active(self):
        if self.revoked:
            raise ExecutionLimitError("tool_scope_closed", "Run Tool execution scope is closed")
        if self.parent is not None:
            self.parent.require_active()

    def checkpoint(self):
        self.revision += 1
        return {"calls": self.calls, "completed": self.completed, "pending_approvals": list(self.pending_approvals),
                "invocations": sorted(self.invocations), "revision": self.revision}

    def binding(self):
        """실행 코드/승인 어댑터 변경 시 호스트가 revision을 올려 이전 승인의 재사용을 막는다."""
        root = self
        while root.parent is not None:
            root = root.parent
        return {"revision": root.runtime.revision, "allowed_tools": list(self.allowed_tools) if self.allowed_tools is not None else None,
                "max_calls": self.max_calls, "approval": root.runtime.authorize is not None,
                "timeout_seconds": self.timeout_seconds, "max_output_chars": self.max_output_chars,
                "classifier": root.runtime.classify is not None,
                "isolated_runner": root.runtime.runner is not None,
                "operation_key": root.runtime.operation_key is not None,
                "retry_safe_tools": list(self.runtime.retry_safe_tools), "max_retries": self.max_retries,
                "retry_delay": self.retry_delay, "argument_constraints": deepcopy(self.argument_constraints)}

    def restore(self, records):
        if records:
            latest = max(records, key=lambda item: (item.get("revision", 0), item.get("calls", 0)))
            self.calls, self.completed = latest.get("calls", 0), latest.get("completed", 0)
            self.pending_approvals = list(latest.get("pending_approvals", []))
            self.invocations = set(latest.get("invocations", []))
            self.revision = latest.get("revision", 0)

    def child(self, *, allowed_tools, max_calls=None, argument_constraints=None):
        """Agent 한도를 추가하되 부모의 승인, 실행기, 원장과 예산을 유지한다."""
        return ToolExecutionScope(self.runtime,
            policy={"allowed_tools": list(allowed_tools), "max_calls": max_calls,
                      "argument_constraints": {} if argument_constraints is None else argument_constraints},
            retry={"max_retries": self.max_retries, "delay_seconds": self.retry_delay},
            operations=self.operations, parent=self)

    def validate(self, tool):
        """모델/효과 호출 전에 호스트 실행 계약을 검사한다."""
        self.require_active()
        from llm.components.tools.constraints import constrained_parameters
        constrained_parameters(tool.parameters, self.argument_constraints.get(tool.name, {}))
        contract = tool.contract
        if contract is None:
            return
        root = self
        while root.parent is not None:
            root = root.parent
        if contract.operation_key_required and self.runtime.operation_key is None:
            raise ExecutionLimitError("tool_contract", "Tool requires an operation key adapter")
        if contract.isolation != "none":
            validate = getattr(self.runtime.runner, "validate_tool", None)
            if validate is None:
                raise ExecutionLimitError("tool_contract", "Tool requires a compatible isolated runner")
            validate(tool.name, contract.isolation)

    def record_completion(self):
        self.completed += 1
        if self.parent is not None:
            self.parent.record_completion()

    async def authorize(self, call: ToolCall, *, decision=None, invocation=None):
        self.require_active()
        if self.allowed_tools is not None and call.name not in self.allowed_tools:
            raise ExecutionLimitError("tool_denied", f"Tool is not allowed: {call.name}")
        identity = json.dumps([call.name, call.arguments], sort_keys=True, ensure_ascii=False)
        pending = decision is not None and identity in self.pending_approvals
        reserved = pending or invocation is not None and invocation in self.invocations
        if not reserved and self.max_calls is not None and self.calls >= self.max_calls:
            raise ExecutionLimitError("tool_budget_exceeded", "Run Tool call budget exceeded")
        # await 이전에 예약하므로 동시에 진행되는 Graph 분기도 한도를 공유한다.
        if pending:
            self.pending_approvals.remove(identity)
        if not reserved:
            self.calls += 1
        if invocation is not None:
            self.invocations.add(invocation)
        try:
            if self.parent is not None:
                await self.parent.authorize(call, decision=decision, invocation=invocation)
            if decision is False:
                raise ExecutionLimitError("tool_denied", f"Tool authorization denied: {call.name}")
            if self.parent is None:
                # 기술적인 거부는 저장된 승인으로도 우회할 수 없다. ASK만 기존 결정을 소비한다.
                if self.runtime.authorize is not None:
                    try:
                        allowed = await self.runtime.authorize(deepcopy(call))
                    except ToolApprovalRequired:
                        if decision is not True:
                            raise
                    else:
                        if allowed is not True:
                            raise ExecutionLimitError("tool_denied", f"Tool authorization denied: {call.name}")
                if decision is not True and (call.contract or {}).get("approval_required"):
                    raise ToolApprovalRequired()
        except ToolApprovalRequired:
            self.pending_approvals.append(identity)
            raise


class ToolInvocationError(CodedError, ValueError):
    """Engine의 durable 호출 연결 오류. 관측 sink나 Tool 실행 실패가 아니다."""

    code = "tool_invocation_invalid"


class ToolExecutor:
    """Tool 핸들러 실행을 하나의 Step으로 표현한다. 저장은 기존 StepManager가 담당한다."""

    def __init__(self, *, timeout_seconds=None, max_output_chars=None):
        if timeout_seconds is not None and (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("Tool timeout must be positive and finite")
        if max_output_chars is not None and (type(max_output_chars) is not int or max_output_chars < 1):
            raise ValueError("Tool output limit must be a positive integer")
        self.timeout_seconds, self.max_output_chars = timeout_seconds, max_output_chars

    def _request_state(self, tool, arguments, context, decision, request_key, classification):
        """권한을 부여하지 않고 기존 체크포인트 연결만 검증한다. 계측 여부와 무관하다."""
        if request_key is not None and (not isinstance(request_key, str) or not request_key.strip()):
            raise ToolInvocationError("Tool request_key must be a nonempty string")
        records = (getattr(context, "checkpoint", None) or {}).get("records", {})
        run = getattr(context, "run", None)
        decisions = getattr(run, "metadata", {}).get("resume", {}).get("decisions", {})
        previous = records.get(request_key, {})

        def tool_approval(record):
            interaction = record.get("interaction", {})
            return (record.get("status") == "waiting" and interaction.get("kind") == "approval"
                    and "tool" in interaction.get("action", {})
                    and "arguments" in interaction.get("action", {}))

        # Workflow continuation과 Tool 권한은 서로 다른 계약이다. 같은 key의
        # approved=True라도 실제 waiting Tool 요청이 아니면 승인으로 해석하지 않는다.
        durable_approval = tool_approval(previous)
        interaction = previous.get("interaction", {})
        binding = interaction.get("binding", {})
        stored_decision = decisions.get(request_key)
        if durable_approval and request_key in decisions:
            # Loop bool / Graph object의 해석은 이미 저장된 선택지 값과 effect를 따른다.
            # 승인 유효성 검사는 InteractionResponse/Engine resume가 계속 소유한다.
            try:
                selected, _ = InteractionRequest.from_dict(interaction).select_decision(stored_decision)
                stored_decision = selected.effect == "approve" if selected.effect in ("approve", "deny") else None
            except (ValueError, TypeError, KeyError) as error:
                raise ToolInvocationError("Durable Tool approval has an invalid interaction decision") from error
        if decision is UNSET:
            decision = stored_decision if durable_approval and request_key in decisions else None

        if durable_approval:
            action = interaction.get("action", {})
            scope = getattr(context, "tool_scope", None)
            if not same_interaction_value(action.get("classification"), classification):
                raise ToolInvocationError("Durable Tool approval classification changed")
            if scope is not None and not same_interaction_value(action.get("policy"), scope.binding()):
                raise ToolInvocationError("Durable Tool approval policy/adapter binding changed")
            if not same_interaction_value(action.get("contract"), asdict(tool.contract) if tool.contract else None):
                raise ToolInvocationError("Durable Tool approval Tool contract changed")
            if scope is not None and not same_interaction_value(
                    action.get("policy", {}).get("argument_constraints", {}), scope.argument_constraints):
                raise ToolInvocationError("Durable Tool approval argument constraints changed")
            if (request_key not in decisions or type(stored_decision) is not bool
                    or type(decision) is not bool or decision != stored_decision
                    or binding.get("key") != request_key
                    or action.get("tool") != tool.name or not same_interaction_value(action.get("arguments"), arguments)):
                raise ToolInvocationError("Durable Tool approval does not match its checkpoint invocation")
            return decision, True

        if request_key in records and decision is not None:
            raise ToolInvocationError("Checkpoint decision is not a durable Tool approval")

        if decision is not None and (request_key is None or request_key not in records):
            # 같은 Tool/인자라도 여러 invocation이 가능하므로 후보에서 key를 추측하지 않는다.
            # 일치 후보는 누락/오류를 검출하는 용도뿐이며 승인 또는 dedup 근거로 사용하지 않는다.
            for key, value in records.items():
                action = value.get("interaction", {}).get("action", {})
                if (key in decisions and tool_approval(value)
                        and action.get("tool") == tool.name and same_interaction_value(action.get("arguments"), arguments)):
                    raise ToolInvocationError("Durable Tool approval resume requires its stable request_key")
        scope = getattr(context, "tool_scope", None)
        identity = json.dumps([tool.name, arguments], sort_keys=True, ensure_ascii=False)
        resumed = decision is not None and scope is not None and identity in scope.pending_approvals
        # 다른 노드의 pause_before는 같은 인자의 pending 요청과 합치지 않는다.
        return decision, False if request_key in records else resumed

    async def _execute(self, tool, arguments, *, result, metadata=None, context=None, decision=None, classification=None,
                       request_key=None):
        ProjectConfig.validate_json(arguments)
        step_id = new_id()
        scope = getattr(context, "tool_scope", None) or ToolExecutionScope(ToolRuntime())
        scope.validate(tool)
        call = ToolCall(tool.name, deepcopy(arguments), step_id,
                        context.project.id if context else None, context.session.id if context else None,
                        context.run.id if context else None,
                        input_message_id=context.run.input_message_id if context else None,
                        contract=asdict(tool.contract) if tool.contract is not None else None,
                        classification=classification)
        # Project의 제한은 모든 엔진이 공유하며 로컬 호출은 좁히기만 한다.
        def narrow(left, right):
            return min(v for v in (left, right) if v is not None) if left is not None or right is not None else None
        seconds = narrow(self.timeout_seconds, scope.timeout_seconds)
        output_limit = narrow(self.max_output_chars, scope.max_output_chars)
        if scope.runtime.operation_key is not None:
            key = scope.runtime.operation_key(deepcopy(call))
            if key is not None:
                if scope.operations is None:
                    raise ValueError("Operation keys require a Session-bound operation service")
                call = replace(call, operation_key=key,
                               idempotency_key=operation_token(call.project_id, call.session_id, key))
        if tool.contract is not None and tool.contract.operation_key_required and call.operation_key is None:
            raise ExecutionLimitError("tool_contract", "Tool operation key cannot be empty")
        yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id, kind="tool", name=tool.name,
                          metadata={**deepcopy(metadata or {}), "arguments": deepcopy(arguments),
                                    "phase": "authorizing", "operation_key": call.operation_key,
                                    "idempotency_key": call.idempotency_key})
        authorized = False
        try:
            deadline = None if seconds is None else asyncio.get_running_loop().time() + seconds
            async with timeout(seconds):
                invocation = (json.dumps([getattr(context, "checkpoint_scope", None), request_key])
                              if request_key is not None else None)
                await scope.authorize(call, decision=decision, invocation=invocation)
            authorized = True
            # yield 바깥 서비스 I/O까지 timeout context가 취소하지 않도록 분리한다.
            yield EngineEvent(EngineEventType.STEP_UPDATED, step_id=step_id,
                              metadata={"phase": "executing", "authorization": "allowed"})
            remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
            if remaining is not None and remaining <= 0:
                raise asyncio.TimeoutError()
            async with timeout(remaining):
                reservation = await scope.operations.claim(call) if call.operation_key is not None else {"reused": False}
            value = reservation.get("result")
            if reservation["reused"]:
                record("tools", "reused", name=tool.name)
            if not reservation["reused"]:
                attempt = 0
                uncertain_attempt = False
                while True:
                    remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
                    if remaining is not None and remaining <= 0:
                        raise asyncio.TimeoutError()
                    try:
                        scope.require_active()
                        record("tools", "executions", retry=attempt > 0, name=tool.name,
                               run_id=call.run_id, step_id=call.step_id)
                        stream = getattr(tool.handler, "execute_events", None)
                        if getattr(tool.handler, "resumable_container", False) is True and callable(stream):
                            if call.operation_key is not None:
                                raise ValueError("An orchestration Tool uses child operation receipts, not a container operation key")
                            from contextlib import aclosing
                            nested = {}
                            async with aclosing(stream(context, deepcopy(arguments), request_key=request_key,
                                    checkpoint_name=(metadata or {}).get("checkpoint_name"),
                                    step_id=step_id, result=nested)) as events:
                                while True:
                                    try:
                                        left = None if deadline is None else deadline - asyncio.get_running_loop().time()
                                        # 저장 소비자를 취소하지 않고 다음 자식 이벤트 대기만 제한한다.
                                        async with timeout(left):
                                            event = await anext(events)
                                    except StopAsyncIteration:
                                        break
                                    if event.type == EngineEventType.PAUSED:
                                        yield EngineEvent(EngineEventType.STEP_CANCELLED, step_id=step_id,
                                            metadata={"phase": "waiting_child"})
                                        result["paused"] = True
                                        yield event
                                        return
                                    yield event
                            value = nested["value"]
                        else:
                            async with timeout(remaining):
                                token = _tool_call.set(call)
                                try:
                                    if scope.runtime.runner is None:
                                        value = await tool.handler(deepcopy(arguments))
                                    else:
                                        value = await scope.runtime.runner(tool, deepcopy(call))
                                finally:
                                    _tool_call.reset(token)
                        break
                    except Exception as error:
                        uncertain_attempt = uncertain_attempt or not (isinstance(error, ToolExecutionError) and error.effect == "none")
                        safe = (isinstance(error, ToolExecutionError) and error.effect == "none"
                                or tool.name in scope.runtime.retry_safe_tools)
                        retryable = isinstance(error, ToolExecutionError) and error.retryable
                        if not safe or not retryable or scope.max_retries is None or attempt >= scope.max_retries:
                            if isinstance(error, ToolExecutionError) and error.effect == "none" and uncertain_attempt:
                                raise ToolExecutionError(str(error), effect="uncertain") from error
                            if isinstance(error, ToolExecutionError) and error.effect == "none" and call.operation_key is not None:
                                try:
                                    await scope.operations.not_applied(call, str(error))
                                except Exception as persistence_error:
                                    raise RuntimeError("Could not persist the no-effect Tool receipt") from persistence_error
                            raise
                        attempt += 1
                        yield EngineEvent(EngineEventType.STEP_UPDATED, step_id=step_id,
                            metadata={"phase": "retrying", "retry_attempt": attempt,
                                      "effect": error.effect, "retry_error": str(error)})
                        remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
                        if remaining is not None and remaining <= 0:
                            raise asyncio.TimeoutError()
                        async with timeout(remaining):
                            if scope.retry_delay is not None:
                                await asyncio.sleep(scope.retry_delay)
            ProjectConfig.validate_json({"result": value})
            content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
            if output_limit is not None and len(content) > output_limit:
                raise ValueError("Tool output limit exceeded")
            if call.operation_key is not None and not reservation["reused"]:
                await scope.operations.complete(call, value)
            result.update(value=deepcopy(value), content=content)
            scope.record_completion()
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except ToolApprovalRequired as request:
            if authorized:
                error = ToolExecutionError("Approval requests must originate before Tool execution", effect="uncertain")
                yield BaseEngine.step_failed_event(step_id, error, code="tool_failed",
                    metadata={"phase": "failed", "error_code": "tool_failed"})
                raise error from request
            # 표시용 문구와 실제 실행 대상은 분리한다. 호출자 제공 source/action은 신뢰하지 않는다.
            request.request = replace(request.request, **call.classification,
                source={"project_id": call.project_id, "session_id": call.session_id,
                        "run_id": call.run_id, "step_id": call.step_id},
                action={"tool": call.name, "arguments": deepcopy(call.arguments),
                        "contract": deepcopy(call.contract), "policy": scope.binding(),
                        "classification": deepcopy(call.classification)})
            yield EngineEvent(EngineEventType.STEP_CANCELLED, step_id=step_id,
                              metadata={"phase": "waiting_approval", "arguments": deepcopy(arguments)})
            raise
        except Exception as error:
            if isinstance(error, asyncio.TimeoutError):
                error = ExecutionLimitError("tool_timeout", "Tool execution or authorization timed out")
            yield BaseEngine.step_failed_event(step_id, error, code="tool_failed",
                message=f"Tool execution failed: {error}",
                metadata={"phase": "failed", "error_code": getattr(error, "code", "tool_failed")})
            raise error
        yield EngineEvent(EngineEventType.STEP_COMPLETED, step_id=step_id,
                          output=EngineOutput(step_id, data=value, step_id=step_id, visibility="internal"),
                          metadata={"phase": "completed", "reused": reservation["reused"]})

    async def execute(self, tool, arguments, *, result, metadata=None, context=None, decision=UNSET,
                      request_key=None):
        """논리 호출 계측은 이 경계 하나에 둔다. 재개 여부는 기존 승인 예약에서 읽는다."""
        from contextlib import aclosing
        from llm.components.tools.constraints import constrained_arguments
        scope = getattr(context, "tool_scope", None)
        arguments = constrained_arguments(tool.parameters, arguments,
            scope.argument_constraints.get(tool.name, {}) if scope is not None else {})
        from llm.components.tools.registry import ToolClassification
        classification = tool.classification or ToolClassification()
        if scope is not None and scope.runtime.classify is not None:
            classification = scope.runtime.classify(ToolCall(tool.name, deepcopy(arguments), "",
                context.project.id, context.session.id, context.run.id,
                contract=asdict(tool.contract) if tool.contract else None))
            if inspect.isawaitable(classification):
                classification = await classification
        if not isinstance(classification, ToolClassification):
            raise TypeError("Trusted classifier must return ToolClassification")
        classification = asdict(classification)
        decision, resumed = self._request_state(tool, arguments, context, decision, request_key, classification)
        if scope is not None and request_key is not None:
            invocation = json.dumps([getattr(context, "checkpoint_scope", None), request_key])
            resumed = resumed or invocation in scope.invocations
        if not resumed:
            record("tools", "requests", name=tool.name)
        started = time.monotonic()
        status, code = "completed", None
        try:
            async with aclosing(self._execute(tool, arguments, result=result, metadata=metadata,
                                              context=context, decision=decision, classification=classification,
                                              request_key=request_key)) as events:
                async for event in events:
                    if event.type == EngineEventType.PAUSED:
                        status = "paused"
                    yield event
            if result.get("paused"):
                status = "paused"
        except ToolApprovalRequired:
            status = "approval_required"
            raise
        except (asyncio.CancelledError, GeneratorExit):
            if not result.get("paused"):
                status = "cancelled"
            raise
        except Exception as error:
            status, code = "failed", stable_error_code(error) or "tool_failed"
            raise
        finally:
            record("tools", status, code=code, name=tool.name, duration_seconds=time.monotonic() - started)
