"""Transport data only: no UI, logging, model calls or retry side effects."""

from llm.core.contracts import ErrorCategory, Confidence, ErrorReason

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from llm.core.model_protocol import StopReason



class HubErrorCode(StrEnum):
    VALIDATION = "hub_validation"


class ErrorOrigin(StrEnum):
    HUB = "hub"
    BACKEND = "llm"
    PROVIDER = "provider"
    TOOL = "tool"
    COMPONENT = "component"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"



@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Detached evidence for one failure, never authority to repeat an action.

    Origin identifies the reporting boundary, not who is to blame. Details keep
    the source diagnostic, including its exception chain/frames; traceback is
    captured only when a local exception is available. Model explanation is an
    annotation and cannot replace these fields or the evidence classification.
    """

    code: str = ""
    message: str = ""
    origin: ErrorOrigin = ErrorOrigin.UNKNOWN
    operation: str = ""
    stage: str = ""
    category: ErrorCategory = ErrorCategory.UNKNOWN
    reason: str = ErrorReason.UNKNOWN.value
    confidence: Confidence = Confidence.UNKNOWN
    evidence: dict[str, Any] = field(default_factory=dict)
    http_status: int | None = None
    exception_type: str = ""
    raw_finish_reason: str = ""
    termination: StopReason = StopReason.UNKNOWN
    detail: str = ""
    traceback: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)
    explanation: str = ""
    recovery: str = ""

    def __str__(self) -> str:
        # Original text for ordinary Python consumers, not a localized UI view.
        return self.message or self.detail or self.code
