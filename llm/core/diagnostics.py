"""Pure projections of owner-supplied diagnostics; classification grants no authority."""

from llm.core.contracts import ErrorCategory, ErrorReason, Confidence

from llm.core.checkpoints import CheckpointErrorCode
from llm.providers.streaming import CompletionErrorCode
from llm.core.models import RunErrorCode
from llm.core.models import StepErrorCode
from llm.core.policies import UsageErrorCode
from llm.providers.codes import ProviderErrorCode
from llm.core.tool_codes import ToolErrorCode
from dataclasses import dataclass, field
from .contracts import Diagnostic, JsonValue
from .model_protocol import StopReason


@dataclass(frozen=True, slots=True)
class ErrorClassification(JsonValue):
    category: str
    reason: str
    confidence: str
    evidence: dict = field(default_factory=dict)


def classify_diagnostic(value: Diagnostic) -> ErrorClassification:
    """Classify documented backend codes only; unfamiliar semantics stay unknown.

    This is a closed observation vocabulary, not an exception/Component registry
    or recovery policy. Owner details are preserved by Diagnostic even when this
    projection cannot classify them. Never infer authority from a category.
    """
    known = {
        ProviderErrorCode.AUTHENTICATION.value: (ErrorCategory.ACCESS, ErrorReason.AUTHENTICATION),
        ProviderErrorCode.RATE_LIMIT.value: (ErrorCategory.SERVICE, ErrorReason.RATE_LIMIT),
        ProviderErrorCode.TIMEOUT.value: (ErrorCategory.SERVICE, ErrorReason.TIMEOUT),
        ProviderErrorCode.CONNECTION.value: (ErrorCategory.SERVICE, ErrorReason.CONNECTION),
        ProviderErrorCode.UNAVAILABLE.value: (ErrorCategory.SERVICE, ErrorReason.UNAVAILABLE),
        ProviderErrorCode.INVALID_RESPONSE.value: (ErrorCategory.CONTRACT, ErrorReason.RESPONSE),
        ProviderErrorCode.EMPTY_RESPONSE.value: (ErrorCategory.CONTRACT, ErrorReason.RESPONSE),
        ProviderErrorCode.INVALID_REQUEST.value: (ErrorCategory.CONTRACT, ErrorReason.REQUEST),
        CompletionErrorCode.INCOMPLETE: (ErrorCategory.EXECUTION, ErrorReason.INCOMPLETE),
        CompletionErrorCode.OUTPUT_LIMIT: (ErrorCategory.RESOURCE, ErrorReason.OUTPUT),
        CompletionErrorCode.TOOL_LIMIT: (ErrorCategory.RESOURCE, ErrorReason.TOOLS),
        CompletionErrorCode.ARGUMENT_LIMIT: (ErrorCategory.RESOURCE, ErrorReason.ARGUMENTS),
        CheckpointErrorCode.CONFLICT: (ErrorCategory.CONTRACT, ErrorReason.REVISION),
        CheckpointErrorCode.PROTECTED: (ErrorCategory.ACCESS, ErrorReason.CHECKPOINT),
        RunErrorCode.TOOL_DENIED: (ErrorCategory.ACCESS, ErrorReason.AUTHORIZATION),
        ToolErrorCode.APPROVAL_REQUIRED: (ErrorCategory.ACCESS, ErrorReason.APPROVAL),
        UsageErrorCode.LIMIT: (ErrorCategory.RESOURCE, ErrorReason.USAGE),
        RunErrorCode.RUN_TIMEOUT: (ErrorCategory.EXECUTION, ErrorReason.TIMEOUT),
        StepErrorCode.TIMEOUT: (ErrorCategory.EXECUTION, ErrorReason.TIMEOUT),
        ProviderErrorCode.CANCELLED.value: (ErrorCategory.EXECUTION, ErrorReason.CANCELLED),
    }
    if value.severity != "error" or value.code not in known:
        return ErrorClassification(ErrorCategory.UNKNOWN, ErrorReason.UNKNOWN, Confidence.UNKNOWN, {"code": value.code})
    category, reason = known[value.code]
    evidence = {"code": value.code}
    if value.code == CompletionErrorCode.INCOMPLETE and value.details.get("termination") == StopReason.OUTPUT_LIMIT:
        return ErrorClassification(ErrorCategory.RESOURCE, ErrorReason.OUTPUT, Confidence.EXPLICIT, {**evidence, "termination": StopReason.OUTPUT_LIMIT.value})
    if value.code == ProviderErrorCode.INVALID_REQUEST.value:
        # Only the provider adapter owns these normalized, cause-local hints.
        hint = value.details.get("request_failure")
        if isinstance(hint, dict) and hint.get("reason") in (ErrorReason.CONTEXT, ErrorReason.PARAMETER):
            category = ErrorCategory.RESOURCE if hint["reason"] == ErrorReason.CONTEXT else ErrorCategory.CONTRACT
            return ErrorClassification(category, hint["reason"], Confidence.INFERRED, {**evidence, **hint})
    return ErrorClassification(category, reason, Confidence.EXPLICIT, evidence)
