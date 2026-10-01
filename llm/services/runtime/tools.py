"""Engine 간에 공유하는 Tool 호출기. 실행 인자·결과·실패를 Step 이벤트로 보고한다."""

import asyncio
import json
import math
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import replace, asdict, field
from typing import Callable, Optional

from llm.compat import dataclass, timeout
from llm.core.contracts import Diagnostic
from llm.core.configuration import UNSET
from llm.core.models import ProjectConfig, new_id
from llm.core.results import EngineOutput
from llm.core.interactions import InteractionRequest, approval_request
from llm.engines.base import EngineEvent, EngineEventType
from llm.services.runtime.policies import ExecutionLimitError
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


_tool_call = ContextVar("llm_tool_call", default=None)


class ToolExecutionError(RuntimeError):
    """핸들러가 보장하는 실패 분류. 효과가 없다는 증거가 있을 때만 effect='none'을 사용한다."""

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
class ToolPolicy:
    """허용 목록, Run 전체 호출 한도, 비동기 승인/실행 어댑터.

    authorize(call)는 정확히 True를 반환해야 실행한다. runner(tool, call)는
    별도 프로세스/호스트에 위임할 수 있는 async 함수다. 기본 실행은 OS sandbox가 아니다.
    승인 대기도 Tool timeout에 포함되며, 정책 오류를 자동 재시도하지 않는다.
    operation_key(call)는 동기 함수로 Session 범위 업무 키 또는 None을 반환한다.
    동일 키의 완료 결과만 재사용하며 불확실한 작업은 명시적인 외부 결과 확인이 필요하다.
    """

    allowed_tools: Optional[tuple[str, ...]] = None
    max_calls: Optional[int] = None
    authorize: Optional[Callable] = None
    runner: Optional[Callable] = None
    operation_key: Optional[Callable] = None
    operation_probe: Optional[Callable] = None
    # 실행 안전성은 신뢰한 개발자가 등록한다. 모델이 JSON으로 권한을 승격할 수 없다.
    retry_safe_tools: tuple[str, ...] = ()
    max_retries: Optional[int] = UNSET
    retry_delay: Optional[float] = UNSET
    _explicit_retry: frozenset = field(init=False, repr=False, compare=False)
    revision: str = "1"
    auto_approve_categories: tuple[str, ...] = ()

    def __post_init__(self):
        explicit = frozenset(k for k in ("max_retries", "retry_delay") if getattr(self, k) is not UNSET)
        object.__setattr__(self, "_explicit_retry", explicit)
        for key in ("max_retries", "retry_delay"):
            if key not in explicit:
                object.__setattr__(self, key, None)
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError("Tool policy revision must be nonempty")
        if not isinstance(self.auto_approve_categories, tuple) or any(not isinstance(v, str) or not v for v in self.auto_approve_categories):
            raise ValueError("auto_approve_categories must be a tuple of category names")
        if self.max_retries is not None and (type(self.max_retries) is not int or self.max_retries < 0):
            raise ValueError("Tool max_retries must be a nonnegative integer")
        if self.retry_delay is not None and (isinstance(self.retry_delay, bool) or not isinstance(self.retry_delay, (int, float))
                or not math.isfinite(self.retry_delay) or self.retry_delay < 0):
            raise ValueError("Tool retry_delay must be finite and nonnegative")
        if not isinstance(self.retry_safe_tools, tuple) or any(not isinstance(n, str) or not n for n in self.retry_safe_tools):
            raise ValueError("retry_safe_tools must be a tuple of names")
        if self.allowed_tools is not None and (not isinstance(self.allowed_tools, tuple)
                or any(not isinstance(name, str) or not name for name in self.allowed_tools)):
            raise ValueError("allowed_tools must be a tuple of names")
        if self.max_calls is not None and (type(self.max_calls) is not int or self.max_calls < 1):
            raise ValueError("max_calls must be positive")
        if any(value is not None and not callable(value) for value in (self.authorize, self.runner, self.operation_key, self.operation_probe)):
            raise TypeError("Tool adapters must be callable")


class ToolExecutionScope:
    """한 Run의 Graph 분기·Agent가 공유하는 호출 예산. asyncio 루프에서만 접근한다."""

    def __init__(self, policy: ToolPolicy, *, operations=None, parent=None):
        self.policy = policy
        self.calls = 0
        self.operations = operations
        self.parent = parent
        self.completed = 0
        self.pending_approvals = []
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
        return {"calls": self.calls, "completed": self.completed, "pending_approvals": list(self.pending_approvals), "revision": self.revision}

    def binding(self):
        """실행 코드/승인 어댑터 변경 시 호스트가 revision을 올려 이전 승인의 재사용을 막는다."""
        root = self
        while root.parent is not None:
            root = root.parent
        return {"revision": root.policy.revision, "auto_approve_categories": list(root.policy.auto_approve_categories), "allowed_tools": list(self.policy.allowed_tools) if self.policy.allowed_tools is not None else None,
                "max_calls": self.policy.max_calls, "approval": root.policy.authorize is not None,
                "isolated_runner": root.policy.runner is not None,
                "operation_key": root.policy.operation_key is not None,
                "retry_safe_tools": list(self.policy.retry_safe_tools), "max_retries": self.policy.max_retries}

    def restore(self, records):
        if records:
            latest = max(records, key=lambda item: (item.get("revision", 0), item.get("calls", 0)))
            self.calls, self.completed = latest.get("calls", 0), latest.get("completed", 0)
            self.pending_approvals = list(latest.get("pending_approvals", []))
            self.revision = latest.get("revision", 0)

    def child(self, *, allowed_tools, max_calls=None):
        """Agent 한도를 추가하되 부모의 승인, 실행기, 원장과 예산을 유지한다."""
        return ToolExecutionScope(replace(self.policy, allowed_tools=tuple(allowed_tools),
                                         max_calls=max_calls, authorize=None),
                                  operations=self.operations, parent=self)

    def validate(self, tool):
        """모델/효과 호출 전에 호스트 실행 계약을 검사한다."""
        self.require_active()
        contract = tool.contract
        if contract is None:
            return
        root = self
        while root.parent is not None:
            root = root.parent
        if contract.approval_required and root.policy.authorize is None:
            raise ExecutionLimitError("tool_contract", "Tool requires an authorization adapter")
        if contract.operation_key_required and self.policy.operation_key is None:
            raise ExecutionLimitError("tool_contract", "Tool requires an operation key adapter")
        if contract.isolation != "none":
            validate = getattr(self.policy.runner, "validate_tool", None)
            if validate is None:
                raise ExecutionLimitError("tool_contract", "Tool requires a compatible isolated runner")
            validate(tool.name, contract.isolation)

    def record_completion(self):
        self.completed += 1
        if self.parent is not None:
            self.parent.record_completion()

    async def authorize(self, call: ToolCall, *, decision=None):
        self.require_active()
        if self.policy.allowed_tools is not None and call.name not in self.policy.allowed_tools:
            raise ExecutionLimitError("tool_denied", f"Tool is not allowed: {call.name}")
        identity = json.dumps([call.name, call.arguments], sort_keys=True, ensure_ascii=False)
        reserved = decision is not None and identity in self.pending_approvals
        if not reserved and self.policy.max_calls is not None and self.calls >= self.policy.max_calls:
            raise ExecutionLimitError("tool_budget_exceeded", "Run Tool call budget exceeded")
        # await 이전에 예약하므로 동시에 진행되는 Graph 분기도 한도를 공유한다.
        if reserved:
            self.pending_approvals.remove(identity)
        else:
            self.calls += 1
        try:
            if self.parent is not None:
                await self.parent.authorize(call, decision=decision)
            if decision is False:
                raise ExecutionLimitError("tool_denied", f"Tool authorization denied: {call.name}")
            if decision is not True and self.policy.authorize is not None and await self.policy.authorize(deepcopy(call)) is not True:
                raise ExecutionLimitError("tool_denied", f"Tool authorization denied: {call.name}")
        except ToolApprovalRequired:
            self.pending_approvals.append(identity)
            raise


class ToolExecutor:
    """Tool 핸들러 실행을 하나의 Step으로 표현한다. 저장은 기존 StepManager가 담당한다."""

    def __init__(self, *, timeout_seconds=None, max_output_chars=None):
        if timeout_seconds is not None and (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("Tool timeout must be positive and finite")
        if max_output_chars is not None and (type(max_output_chars) is not int or max_output_chars < 1):
            raise ValueError("Tool output limit must be a positive integer")
        self.timeout_seconds, self.max_output_chars = timeout_seconds, max_output_chars

    async def execute(self, tool, arguments, *, result, metadata=None, context=None, decision=None):
        ProjectConfig.validate_settings(arguments)
        step_id = new_id()
        scope = getattr(context, "tool_scope", None) or ToolExecutionScope(ToolPolicy())
        scope.validate(tool)
        call = ToolCall(tool.name, deepcopy(arguments), step_id,
                        context.project.id if context else None, context.session.id if context else None,
                        context.run.id if context else None,
                        input_message_id=context.run.input_message_id if context else None,
                        contract=asdict(tool.contract) if tool.contract is not None else None)
        if scope.policy.operation_key is not None:
            key = scope.policy.operation_key(deepcopy(call))
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
            deadline = None if self.timeout_seconds is None else asyncio.get_running_loop().time() + self.timeout_seconds
            async with timeout(self.timeout_seconds):
                await scope.authorize(call, decision=decision)
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
            if not reservation["reused"]:
                attempt = 0
                uncertain_attempt = False
                while True:
                    remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
                    if remaining is not None and remaining <= 0:
                        raise asyncio.TimeoutError()
                    try:
                        async with timeout(remaining):
                            token = _tool_call.set(call)
                            try:
                                scope.require_active()
                                if scope.policy.runner is None:
                                    value = await tool.handler(deepcopy(arguments))
                                else:
                                    value = await scope.policy.runner(tool, deepcopy(call))
                            finally:
                                _tool_call.reset(token)
                        break
                    except Exception as error:
                        uncertain_attempt = uncertain_attempt or not (isinstance(error, ToolExecutionError) and error.effect == "none")
                        safe = (isinstance(error, ToolExecutionError) and error.effect == "none"
                                or tool.name in scope.policy.retry_safe_tools)
                        retryable = isinstance(error, ToolExecutionError) and error.retryable
                        if not safe or not retryable or scope.policy.max_retries is None or attempt >= scope.policy.max_retries:
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
                            if scope.policy.retry_delay is not None:
                                await asyncio.sleep(scope.policy.retry_delay)
            ProjectConfig.validate_settings({"result": value})
            content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
            if self.max_output_chars is not None and len(content) > self.max_output_chars:
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
                yield EngineEvent(EngineEventType.STEP_FAILED, step_id=step_id,
                                  error=str(error), metadata={"phase": "failed", "error_code": "tool_failed"})
                raise error from request
            # 표시용 문구와 실제 실행 대상은 분리한다. 호출자 제공 source/action은 신뢰하지 않는다.
            request.request = replace(request.request,
                source={"project_id": call.project_id, "session_id": call.session_id,
                        "run_id": call.run_id, "step_id": call.step_id},
                action={"tool": call.name, "arguments": deepcopy(call.arguments),
                        "contract": deepcopy(call.contract), "policy": scope.binding(),
                        "auto_approval_allowed": request.request.category in scope.binding()["auto_approve_categories"]})
            yield EngineEvent(EngineEventType.STEP_CANCELLED, step_id=step_id,
                              metadata={"phase": "waiting_approval", "arguments": deepcopy(arguments)})
            raise
        except Exception as error:
            if isinstance(error, asyncio.TimeoutError):
                error = ExecutionLimitError("tool_timeout", "Tool execution or authorization timed out")
            yield EngineEvent(EngineEventType.STEP_FAILED, step_id=step_id,
                              error=f"Tool execution failed: {error}",
                              diagnostic=getattr(error, "diagnostic", Diagnostic.from_exception(error, code="tool_failed")),
                              metadata={"phase": "failed", "error_code": getattr(error, "code", "tool_failed")})
            raise error
        yield EngineEvent(EngineEventType.STEP_COMPLETED, step_id=step_id,
                          output=EngineOutput(step_id, data=value, step_id=step_id, visibility="internal"),
                          metadata={"phase": "completed", "reused": reservation["reused"]})
