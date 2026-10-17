"""Pure projections of owner-supplied diagnostics; classification grants no authority."""

from dataclasses import dataclass, field
from .contracts import Diagnostic, JsonValue


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
        "provider_authentication": ("access", "authentication"),
        "provider_rate_limit": ("service", "rate_limit"),
        "provider_timeout": ("service", "timeout"),
        "provider_connection": ("service", "connection"),
        "provider_unavailable": ("service", "unavailable"),
        "provider_invalid_response": ("contract", "response"),
        "provider_empty_response": ("contract", "response"),
        "provider_invalid_request": ("contract", "request"),
        "completion_incomplete": ("execution", "incomplete"),
        "completion_output_limit": ("resource", "output"),
        "completion_tool_limit": ("resource", "tools"),
        "completion_argument_limit": ("resource", "arguments"),
        "checkpoint_conflict": ("contract", "revision"),
        "checkpoint_protected": ("access", "checkpoint"),
        "tool_denied": ("access", "authorization"),
        "tool_approval_required": ("access", "approval"),
        "usage_limit": ("resource", "usage"),
        "run_timeout": ("execution", "timeout"),
        "step_timeout": ("execution", "timeout"),
        "provider_cancelled": ("execution", "cancelled"),
    }
    if value.severity != "error" or value.code not in known:
        return ErrorClassification("unknown", "unknown", "unknown", {"code": value.code})
    category, reason = known[value.code]
    evidence = {"code": value.code}
    if value.code == "completion_incomplete" and value.details.get("finish_reason") == "length":
        return ErrorClassification("resource", "output", "explicit", {**evidence, "finish_reason": "length"})
    if value.code == "provider_invalid_request":
        # Only the provider adapter owns these normalized, cause-local hints.
        hint = value.details.get("request_failure")
        if isinstance(hint, dict) and hint.get("reason") in ("context", "parameter"):
            category = "resource" if hint["reason"] == "context" else "contract"
            return ErrorClassification(category, hint["reason"], "inferred", {**evidence, **hint})
    return ErrorClassification(category, reason, "explicit", evidence)
