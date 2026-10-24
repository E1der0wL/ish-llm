"""Tool failure classification and approval signals; no retry or storage policy."""

from typing import Optional
from llm.core.contracts import Diagnostic
from llm.core.interactions import InteractionOption, InteractionRequest, approval_request
from llm.core.tool_codes import ToolErrorCode
from llm.errors import CodedError


class ToolExecutionError(CodedError, RuntimeError):
    """핸들러가 보장하는 실패 분류. 효과가 없다는 증거가 있을 때만 effect='none'을 사용한다."""

    INVALID_CLASSIFICATION = "Invalid Tool failure classification"
    INVALID_RECOVERY = "Recoverable Tool errors require confirmed absence of effects"
    EFFECT = "effect"
    RETRYABLE = "retryable"
    RECOVERABLE = "recoverable"
    RETRY_DISABLED = False
    RECOVERY_PRESENT = True

    code = ToolErrorCode.FAILED
    NO_EFFECT, UNCERTAIN_EFFECT = "none", "uncertain"
    EFFECTS = (NO_EFFECT, UNCERTAIN_EFFECT)

    def __init__(
        self, message: str, *, effect=UNCERTAIN_EFFECT, retryable=RETRY_DISABLED, recoverable=RETRY_DISABLED
    ):
        if effect not in ToolExecutionError.EFFECTS or type(retryable) is not bool:
            raise ValueError(ToolExecutionError.INVALID_CLASSIFICATION)
        if type(recoverable) is not bool or recoverable and effect != ToolExecutionError.NO_EFFECT:
            raise ValueError(ToolExecutionError.INVALID_RECOVERY)
        super().__init__(message)
        self.effect, self.retryable = effect, retryable
        self.recoverable = recoverable

    @property
    def diagnostic(self):
        return Diagnostic(
            ToolExecutionError.code,
            str(self),
            details={
                ToolExecutionError.EFFECT: self.effect,
                ToolExecutionError.RETRYABLE: self.retryable,
                **(
                    {ToolExecutionError.RECOVERABLE: ToolExecutionError.RECOVERY_PRESENT}
                    if self.recoverable
                    else {}
                ),
            },
        )


class ToolApprovalRequired(RuntimeError):
    """authorize가 발생시키면 공통 요청을 체크포인트에 저장하고 Run을 일시정지한다."""

    DEFAULT_MESSAGE = "Tool 실행 승인"
    ABSENT = None
    INVALID_REQUEST = "request must be an InteractionRequest"
    INVALID_OPTIONS = "Tool approval options must be boolean and cannot modify arguments"
    ALLOWED = True
    DENIED = False

    def __init__(self, message=DEFAULT_MESSAGE, *, request: Optional[InteractionRequest] = ABSENT):
        if request is not ToolApprovalRequired.ABSENT and not isinstance(request, InteractionRequest):
            raise TypeError(ToolApprovalRequired.INVALID_REQUEST)
        self.request = (
            InteractionRequest.from_dict(request.to_dict())
            if request is not ToolApprovalRequired.ABSENT
            else approval_request(str(message))
        )
        if (
            self.request.kind != InteractionRequest.KIND_APPROVAL
            or self.request.input_schema is not ToolApprovalRequired.ABSENT
            or any(
                type(o.value) is not bool
                or o.effect
                != (InteractionOption.EFFECT_APPROVE if o.value else InteractionOption.EFFECT_DENY)
                for o in self.request.options
            )
        ):
            raise ValueError(ToolApprovalRequired.INVALID_OPTIONS)
        super().__init__(self.request.title)


class ToolInvocationError(CodedError, ValueError):
    """Engine의 durable 호출 연결 오류. 관측 sink나 Tool 실행 실패가 아니다."""

    code = ToolErrorCode.INVOCATION_INVALID
