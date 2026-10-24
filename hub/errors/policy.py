"""Pure recovery decisions. Classification describes a cause, never replay safety."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

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
    NO_DEADLINE: ClassVar[None] = None

    response_received: bool = False
    effects: Effects = Effects.UNKNOWN
    retry_safe: bool = False
    cancelled: bool = False
    attempts: int = 0
    maximum: int = 0
    deadline: float | None = NO_DEADLINE
    now: float = 0
    # Only the backend Tool selector boundary supplies admission. Classification
    # and model output cannot grant it or invent a no-effect receipt.
    backend_admitted: bool = False
    retryable: bool = False
    changeable: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return (
            self.cancelled
            or self.response_received
            or self.attempts >= self.maximum
            or self.deadline is not self.NO_DEADLINE
            and self.now >= self.deadline
        )


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str


class RecoveryReason(StrEnum):
    BACKEND_ADMISSION_REQUIRED = "backend_admission_required"
    ACCESS_REQUIRES_USER_ACTION = "access_requires_user_action"
    BACKEND_SAFE_TRANSIENT = "backend_safe_transient"
    BACKEND_DECLARED_FIELDS = "backend_declared_fields"
    NO_SAFE_CORRECTION = "no_safe_correction"


class RecoveryPolicy:
    @staticmethod
    def tool(error: ErrorInfo, facts: RecoveryFacts) -> RecoveryDecision:
        if facts.blocked or not facts.backend_admitted:
            return RecoveryDecision(
                RecoveryAction.STOP, RecoveryReason.BACKEND_ADMISSION_REQUIRED.value
            )
        if error.category == ErrorCategory.ACCESS:
            return RecoveryDecision(
                RecoveryAction.STOP, RecoveryReason.ACCESS_REQUIRES_USER_ACTION.value
            )
        if facts.retryable and (facts.effects == Effects.NONE or facts.retry_safe):
            return RecoveryDecision(
                RecoveryAction.RETRY, RecoveryReason.BACKEND_SAFE_TRANSIENT.value
            )
        if facts.changeable and facts.effects == Effects.NONE:
            return RecoveryDecision(
                RecoveryAction.REPAIR_ARGUMENTS,
                RecoveryReason.BACKEND_DECLARED_FIELDS.value,
            )
        return RecoveryDecision(
            RecoveryAction.STOP, RecoveryReason.NO_SAFE_CORRECTION.value
        )
