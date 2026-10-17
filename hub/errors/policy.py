"""Pure recovery decisions. Classification describes a cause, never replay safety."""

from dataclasses import dataclass
from enum import StrEnum

from .contracts import ErrorCategory, ErrorInfo


class RecoveryAction(StrEnum):
    STOP = "stop"
    RETRY = "retry"
    REPAIR_ARGUMENTS = "repair_arguments"


class Effects(StrEnum):
    NONE = "none"
    APPLIED = "applied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RecoveryFacts:
    response_received: bool = False
    effects: Effects = Effects.UNKNOWN
    retry_safe: bool = False
    cancelled: bool = False
    attempts: int = 0
    maximum: int = 0
    deadline: float | None = None
    now: float = 0
    # Only the backend Tool selector boundary supplies admission. Classification
    # and model output cannot grant it or invent a no-effect receipt.
    backend_admitted: bool = False
    retryable: bool = False
    changeable: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return (self.cancelled or self.response_received or self.attempts >= self.maximum
                or self.deadline is not None and self.now >= self.deadline)


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str


class RecoveryPolicy:
    @staticmethod
    def tool(error: ErrorInfo, facts: RecoveryFacts) -> RecoveryDecision:
        if facts.blocked or not facts.backend_admitted:
            return RecoveryDecision(RecoveryAction.STOP, "backend_admission_required")
        if error.category == ErrorCategory.ACCESS:
            return RecoveryDecision(RecoveryAction.STOP, "access_requires_user_action")
        if facts.retryable and (facts.effects == Effects.NONE or facts.retry_safe):
            return RecoveryDecision(RecoveryAction.RETRY, "backend_safe_transient")
        if facts.changeable and facts.effects == Effects.NONE:
            return RecoveryDecision(RecoveryAction.REPAIR_ARGUMENTS, "backend_declared_fields")
        return RecoveryDecision(RecoveryAction.STOP, "no_safe_correction")
