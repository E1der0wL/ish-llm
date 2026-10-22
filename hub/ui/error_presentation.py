"""Localized error views. Presentation never classifies or decides recovery."""

from llm.core.contracts import ErrorReason
from ..backend.execution.provider_recovery import RecoveryState

from llm.components.memory import MemoryConflictError

from llm.core.plans import PlanBlockerCode

from hub.errors.operations import OperationCode

from llm.core.checkpoints import CheckpointErrorCode
from llm.providers.streaming import CompletionErrorCode
from llm.core.models import RunErrorCode
from llm.core.models import StepErrorCode
from ..errors.adapters import from_exception
from ..errors.contracts import ErrorInfo
from llm.core.model_protocol import StopReason
from llm.core.tool_codes import ToolErrorCode


_GUIDANCE = {reason: "failure_" + reason for reason in (
    ErrorReason.UNKNOWN, ErrorReason.RATE_LIMIT, ErrorReason.UNAVAILABLE, ErrorReason.AUTHENTICATION, ErrorReason.PERMISSION, ErrorReason.TIMEOUT, ErrorReason.CONNECTION,
    ErrorReason.REQUEST, ErrorReason.CONTEXT, ErrorReason.BUDGET, ErrorReason.FILE_MISSING, ErrorReason.EMPTY, ErrorReason.COMPLETION_INVALID, ErrorReason.COMPLETION_LENGTH,
    ErrorReason.COMPLETION_FILTERED, ErrorReason.COMPLETION_INCOMPLETE, ErrorReason.COMPLETION_OUTPUT_LIMIT, ErrorReason.COMPLETION_TOOL_LIMIT,
    ErrorReason.COMPLETION_ARGUMENT_LIMIT, ErrorReason.STEP_TIMEOUT, ErrorReason.REQUEST_SIZE, ErrorReason.DEPENDENCY, ErrorReason.COMMAND, ErrorReason.TOOL_FAILED,
    ErrorReason.VALIDATION, ErrorReason.DISCONNECTED, ErrorReason.TOOL_ARGUMENTS, ErrorReason.TOOL_CONTRACT, ErrorReason.LIFECYCLE, ErrorReason.SANDBOX,
    ErrorReason.OPERATION_CONFLICT, ErrorReason.OPERATION_UNCERTAIN,
)}
_GUIDANCE.update({
    ErrorReason.AUTHORIZATION: "failure_permission", ErrorReason.APPROVAL: "failure_permission", ErrorReason.RESPONSE: "failure_empty",
    ErrorReason.PARAMETER: "failure_request", ErrorReason.INCOMPLETE: "failure_completion_incomplete", ErrorReason.OUTPUT: "failure_completion_output_limit",
    ErrorReason.TOOLS: "failure_completion_tool_limit", ErrorReason.ARGUMENTS: "failure_completion_argument_limit", ErrorReason.USAGE: "failure_budget",
    ErrorReason.REVISION: "failure_checkpoint_conflict", ErrorReason.CHECKPOINT: "failure_checkpoint_protected",
})
_OUTCOMES = frozenset((RecoveryState.NO_CORRECTION, RecoveryState.SUMMARY_INCOMPLETE, RecoveryState.NOT_REDUCED, RecoveryState.INVALID_ANALYSIS,
                       RecoveryState.ANALYSIS_FAILED, RecoveryState.EXHAUSTED, RecoveryState.REJECTED))


def plain(text: str, maximum: int = 2000) -> str:
    return "".join(c for c in text if c.isprintable() or c == "\n")[:maximum]


def error_text(error: ErrorInfo | Exception | str) -> str:
    """Small notices retain the original validation/operation message."""
    value = from_exception(error)
    blockers = value.details.get("blockers", ())
    messages = [item.get("message", "") for item in blockers if isinstance(item, dict)] if isinstance(blockers, (list, tuple)) else []
    return plain("\n".join(dict.fromkeys([str(value), *(m for m in messages if m and m not in str(value))])))


_OPERATION_GUIDANCE = {
    OperationCode.BUSY: "operation_busy",
    OperationCode.CHANGED: "operation_changed",
    OperationCode.NOT_RESUMABLE: "operation_not_resumable",
    OperationCode.EXISTING_RAG_JOB: "operation_existing_rag_job",
    OperationCode.EDIT_UNAVAILABLE: "history_edit_unavailable",
    CheckpointErrorCode.CONFLICT: "operation_changed",
    MemoryConflictError.code: "operation_changed",
    PlanBlockerCode.INTERACTION_REQUIRED: "operation_interaction",
    PlanBlockerCode.ACTIVE_RUN: "operation_busy", PlanBlockerCode.ACTIVE_WORKER: "operation_busy",
    CheckpointErrorCode.CONFIGURATION: "operation_configuration",
    CheckpointErrorCode.CORRUPT: "operation_checkpoint_unavailable",
    CheckpointErrorCode.MISSING: "operation_checkpoint_unavailable",
    CheckpointErrorCode.DELETED: "operation_checkpoint_unavailable",
    PlanBlockerCode.INPUT_UNAVAILABLE: "operation_checkpoint_unavailable",
    PlanBlockerCode.ENGINE_UNAVAILABLE: "operation_engine",
    PlanBlockerCode.RESTART_RESERVED: "operation_already_resumed",
    PlanBlockerCode.RESTART_UNPROVEN: "operation_restart_unproven",
    PlanBlockerCode.NOT_RESUMABLE: "operation_not_resumable",
    PlanBlockerCode.RESUME_REQUIRED: "operation_abandon_required",
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
    if value.code == CheckpointErrorCode.PROTECTED:
        return t("operation_not_resumable")
    if value.reason == ErrorReason.VALIDATION:
        return error_text(value)
    if value.reason in _GUIDANCE and value.reason != ErrorReason.UNKNOWN:
        return t(_GUIDANCE[value.reason])
    return t("operation_failed")


_CODE_GUIDANCE = {
    CompletionErrorCode.INVALID_RESPONSE: "failure_completion_invalid", ToolErrorCode.INVOCATION_INVALID: "failure_tool_arguments",
    RunErrorCode.TOOL_CONTRACT: "failure_tool_contract", RunErrorCode.SANDBOX_UNAVAILABLE: "failure_sandbox",
    RunErrorCode.OPERATION_UNCERTAIN: "failure_operation_uncertain", RunErrorCode.OPERATION_CONFLICT: "failure_operation_conflict",
    ToolErrorCode.FAILED: "failure_tool_failed", StepErrorCode.TIMEOUT: "failure_step_timeout",
}


def failure_text(error: ErrorInfo, t) -> str:
    key = _GUIDANCE.get(error.reason, "failure_unknown")
    if error.code == CompletionErrorCode.INCOMPLETE:
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
