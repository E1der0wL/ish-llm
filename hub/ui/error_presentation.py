"""Localized error views. Presentation never classifies or decides recovery."""

from ..errors.adapters import from_exception
from ..errors.contracts import ErrorInfo
from llm.core.model_protocol import StopReason


_GUIDANCE = {reason: "failure_" + reason for reason in (
    "unknown", "rate_limit", "unavailable", "authentication", "permission", "timeout", "connection",
    "request", "context", "budget", "file_missing", "empty", "completion_invalid", "completion_length",
    "completion_filtered", "completion_incomplete", "completion_output_limit", "completion_tool_limit",
    "completion_argument_limit", "step_timeout", "request_size", "dependency", "command", "tool_failed",
    "validation", "disconnected", "tool_arguments", "tool_contract", "lifecycle", "sandbox",
    "operation_conflict", "operation_uncertain",
)}
_GUIDANCE.update({
    "authorization": "failure_permission", "approval": "failure_permission", "response": "failure_empty",
    "parameter": "failure_request", "incomplete": "failure_completion_incomplete", "output": "failure_completion_output_limit",
    "tools": "failure_completion_tool_limit", "arguments": "failure_completion_argument_limit", "usage": "failure_budget",
    "revision": "failure_checkpoint_conflict", "checkpoint": "failure_checkpoint_protected",
})
_OUTCOMES = frozenset(("no_correction", "summary_incomplete", "not_reduced", "invalid_analysis",
                       "analysis_failed", "exhausted", "rejected"))


def plain(text: str, maximum: int = 2000) -> str:
    return "".join(c for c in text if c.isprintable() or c == "\n")[:maximum]


def error_text(error: ErrorInfo | Exception | str) -> str:
    """Small notices retain the original validation/operation message."""
    value = from_exception(error)
    blockers = value.details.get("blockers", ())
    messages = [item.get("message", "") for item in blockers if isinstance(item, dict)] if isinstance(blockers, (list, tuple)) else []
    return plain("\n".join(dict.fromkeys([str(value), *(m for m in messages if m and m not in str(value))])))


_OPERATION_GUIDANCE = {
    "hub_operation_busy": "operation_busy",
    "hub_operation_changed": "operation_changed",
    "hub_not_resumable": "operation_not_resumable",
    "hub_existing_rag_job": "operation_existing_rag_job",
    "hub_edit_unavailable": "history_edit_unavailable",
    "checkpoint_conflict": "operation_changed",
    "memory_conflict": "operation_changed",
    "interaction_required": "operation_interaction",
    "active_run": "operation_busy", "active_worker": "operation_busy",
    "checkpoint_configuration": "operation_configuration",
    "checkpoint_corrupt": "operation_checkpoint_unavailable",
    "checkpoint_missing": "operation_checkpoint_unavailable",
    "checkpoint_deleted": "operation_checkpoint_unavailable",
    "input_unavailable": "operation_checkpoint_unavailable",
    "engine_unavailable": "operation_engine",
    "restart_reserved": "operation_already_resumed",
    "restart_unproven": "operation_restart_unproven",
    "not_resumable": "operation_not_resumable",
    "resume_required": "operation_abandon_required",
}


def operation_notice(error, t):
    """User action guidance from structured contracts, never exception text matching."""
    if isinstance(error, str):
        return plain(error)  # Hub-owned localized validation/status text.
    value = from_exception(error)
    codes = [value.code]
    blockers = value.details.get("blockers", ())
    if isinstance(blockers, (list, tuple)):
        codes.extend(b.get("code", "") for b in blockers if isinstance(b, dict))
    keys = [_OPERATION_GUIDANCE[c] for c in codes if c in _OPERATION_GUIDANCE]
    if keys:
        return "\n".join(t(key) for key in dict.fromkeys(keys))
    if value.code == "checkpoint_protected":
        return t("operation_not_resumable")
    if value.reason == "validation":
        return error_text(value)
    if value.reason in _GUIDANCE and value.reason != "unknown":
        return t(_GUIDANCE[value.reason])
    return t("operation_failed")


_CODE_GUIDANCE = {
    "completion_invalid_response": "failure_completion_invalid", "tool_invocation_invalid": "failure_tool_arguments",
    "tool_contract": "failure_tool_contract", "sandbox_unavailable": "failure_sandbox",
    "operation_uncertain": "failure_operation_uncertain", "operation_conflict": "failure_operation_conflict",
    "tool_failed": "failure_tool_failed", "step_timeout": "failure_step_timeout",
}


def failure_text(error: ErrorInfo, t) -> str:
    key = _GUIDANCE.get(error.reason, "failure_unknown")
    if error.code == "completion_incomplete":
        key = {StopReason.OUTPUT_LIMIT: "failure_completion_length", StopReason.BLOCKED: "failure_completion_filtered"}.get(error.termination, key)
    elif error.code in _CODE_GUIDANCE:
        key = _CODE_GUIDANCE[error.code]
    text = t(key)
    evidence = [error.code] if error.code else []
    if error.http_status is not None:
        evidence.append(f"HTTP {error.http_status}")
    if error.exception_type:
        evidence.append(error.exception_type)
    if error.raw_finish_reason:
        evidence.append(f"finish_reason={error.raw_finish_reason}")
    if evidence:
        text += "\n\n" + t("failure_evidence", evidence=" · ".join(evidence))
    for title, value in (("failure_server_detail", error.detail), ("failure_model_analysis", error.explanation)):
        if value:
            # Evidence/model text stays literal Markdown code, never executable XML.
            text += "\n\n" + t(title) + "\n" + "\n".join("    " + line for line in plain(value).splitlines())
    if error.recovery in _OUTCOMES:
        text += "\n\n" + t("recovery_result_" + error.recovery)
    return text + "\n" + t("failure_details")
