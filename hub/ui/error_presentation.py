"""Localized error views. Presentation never classifies or decides recovery."""

from hub.locales.keys import MessageKey, MessagePattern

from llm.core.contracts import ErrorReason
from ..backend.execution.provider_recovery import RecoveryState

from llm.components.memory import MemoryConflictError

from llm.core.plans import PlanBlockerCode

from hub.errors.operations import OperationCode

from llm.core.checkpoints import CheckpointErrorCode, CheckpointRejectionDetails
from llm.providers.streaming import CompletionErrorCode
from llm.core.models import RunErrorCode
from llm.core.models import StepErrorCode
from ..errors.adapters import from_exception
from ..errors.contracts import ErrorInfo
from llm.core.model_protocol import StopReason
from llm.core.tool_codes import ToolErrorCode
from llm.engines.loop.review import LoopReview


_GUIDANCE = {
    reason: MessagePattern.failure(reason)
    for reason in (
        ErrorReason.UNKNOWN,
        ErrorReason.RATE_LIMIT,
        ErrorReason.UNAVAILABLE,
        ErrorReason.AUTHENTICATION,
        ErrorReason.PERMISSION,
        ErrorReason.TIMEOUT,
        ErrorReason.CONNECTION,
        ErrorReason.REQUEST,
        ErrorReason.CONTEXT,
        ErrorReason.BUDGET,
        ErrorReason.FILE_MISSING,
        ErrorReason.EMPTY,
        ErrorReason.COMPLETION_INVALID,
        ErrorReason.COMPLETION_LENGTH,
        ErrorReason.COMPLETION_FILTERED,
        ErrorReason.COMPLETION_INCOMPLETE,
        ErrorReason.COMPLETION_OUTPUT_LIMIT,
        ErrorReason.COMPLETION_TOOL_LIMIT,
        ErrorReason.COMPLETION_ARGUMENT_LIMIT,
        ErrorReason.STEP_TIMEOUT,
        ErrorReason.REQUEST_SIZE,
        ErrorReason.DEPENDENCY,
        ErrorReason.COMMAND,
        ErrorReason.TOOL_FAILED,
        ErrorReason.VALIDATION,
        ErrorReason.DISCONNECTED,
        ErrorReason.TOOL_ARGUMENTS,
        ErrorReason.TOOL_CONTRACT,
        ErrorReason.LIFECYCLE,
        ErrorReason.SANDBOX,
        ErrorReason.OPERATION_CONFLICT,
        ErrorReason.OPERATION_UNCERTAIN,
    )
}
_GUIDANCE.update(
    {
        ErrorReason.AUTHORIZATION: MessageKey.FAILURE_PERMISSION,
        ErrorReason.APPROVAL: MessageKey.FAILURE_PERMISSION,
        ErrorReason.RESPONSE: MessageKey.FAILURE_EMPTY,
        ErrorReason.PARAMETER: MessageKey.FAILURE_REQUEST,
        ErrorReason.INCOMPLETE: MessageKey.FAILURE_COMPLETION_INCOMPLETE,
        ErrorReason.OUTPUT: MessageKey.FAILURE_COMPLETION_OUTPUT_LIMIT,
        ErrorReason.TOOLS: MessageKey.FAILURE_COMPLETION_TOOL_LIMIT,
        ErrorReason.ARGUMENTS: MessageKey.FAILURE_COMPLETION_ARGUMENT_LIMIT,
        ErrorReason.USAGE: MessageKey.FAILURE_BUDGET,
        ErrorReason.REVISION: MessageKey.FAILURE_CHECKPOINT_CONFLICT,
        ErrorReason.CHECKPOINT: MessageKey.FAILURE_CHECKPOINT_PROTECTED,
    }
)
_OUTCOMES = frozenset(
    (
        RecoveryState.NO_CORRECTION,
        RecoveryState.SUMMARY_INCOMPLETE,
        RecoveryState.NOT_REDUCED,
        RecoveryState.INVALID_ANALYSIS,
        RecoveryState.ANALYSIS_FAILED,
        RecoveryState.EXHAUSTED,
        RecoveryState.REJECTED,
    )
)


class ErrorPresentation:
    MAXIMUM_TEXT = 2000
    EMPTY = ""
    NEWLINE = "\n"
    PARAGRAPH = NEWLINE + NEWLINE
    EVIDENCE_SEPARATOR = " • "
    HTTP_STATUS = "HTTP {status}"
    FINISH_REASON = "finish_reason={reason}"
    LITERAL_INDENT = "    "
    INCOMPLETE_GUIDANCE = {
        StopReason.OUTPUT_LIMIT: MessageKey.FAILURE_COMPLETION_LENGTH,
        StopReason.BLOCKED: MessageKey.FAILURE_COMPLETION_FILTERED,
    }


def plain(text: str, maximum: int = ErrorPresentation.MAXIMUM_TEXT) -> str:
    return ErrorPresentation.EMPTY.join(
        c for c in text if c.isprintable() or c == ErrorPresentation.NEWLINE
    )[:maximum]


def error_text(error: ErrorInfo | Exception | str) -> str:
    """Small notices retain the original validation/operation message."""
    value = from_exception(error)
    messages = [
        item.message
        for item in CheckpointRejectionDetails.observed_blockers(value.details)
    ]
    return plain(
        ErrorPresentation.NEWLINE.join(
            dict.fromkeys(
                [str(value), *(m for m in messages if m and m not in str(value))]
            )
        )
    )


_OPERATION_GUIDANCE = {
    OperationCode.BUSY: MessageKey.OPERATION_BUSY,
    OperationCode.CHANGED: MessageKey.OPERATION_CHANGED,
    OperationCode.NOT_RESUMABLE: MessageKey.OPERATION_NOT_RESUMABLE,
    OperationCode.EXISTING_RAG_JOB: MessageKey.OPERATION_EXISTING_RAG_JOB,
    OperationCode.EDIT_UNAVAILABLE: MessageKey.HISTORY_EDIT_UNAVAILABLE,
    CheckpointErrorCode.CONFLICT: MessageKey.OPERATION_CHANGED,
    MemoryConflictError.code: MessageKey.OPERATION_CHANGED,
    PlanBlockerCode.INTERACTION_REQUIRED: MessageKey.OPERATION_INTERACTION,
    PlanBlockerCode.ACTIVE_RUN: MessageKey.OPERATION_BUSY,
    PlanBlockerCode.ACTIVE_WORKER: MessageKey.OPERATION_BUSY,
    CheckpointErrorCode.CONFIGURATION: MessageKey.OPERATION_CONFIGURATION,
    CheckpointErrorCode.CORRUPT: MessageKey.OPERATION_CHECKPOINT_UNAVAILABLE,
    CheckpointErrorCode.MISSING: MessageKey.OPERATION_CHECKPOINT_UNAVAILABLE,
    CheckpointErrorCode.DELETED: MessageKey.OPERATION_CHECKPOINT_UNAVAILABLE,
    PlanBlockerCode.INPUT_UNAVAILABLE: MessageKey.OPERATION_CHECKPOINT_UNAVAILABLE,
    PlanBlockerCode.ENGINE_UNAVAILABLE: MessageKey.OPERATION_ENGINE,
    PlanBlockerCode.RESTART_RESERVED: MessageKey.OPERATION_ALREADY_RESUMED,
    PlanBlockerCode.RESTART_UNPROVEN: MessageKey.OPERATION_RESTART_UNPROVEN,
    PlanBlockerCode.NOT_RESUMABLE: MessageKey.OPERATION_NOT_RESUMABLE,
    PlanBlockerCode.RESUME_REQUIRED: MessageKey.OPERATION_ABANDON_REQUIRED,
}


def operation_notice(error, t):
    """User action guidance from structured contracts, never exception text matching."""
    if isinstance(error, str):
        return plain(error)  # Hub-owned localized validation/status text.
    value = from_exception(error)
    codes = [value.code]
    codes.extend(
        item.code
        for item in CheckpointRejectionDetails.observed_blockers(value.details)
    )
    keys = [_OPERATION_GUIDANCE[c] for c in codes if c in _OPERATION_GUIDANCE]
    if keys:
        return ErrorPresentation.NEWLINE.join(t(key) for key in dict.fromkeys(keys))
    if value.code == CheckpointErrorCode.PROTECTED:
        return t(MessageKey.OPERATION_NOT_RESUMABLE)
    if value.reason == ErrorReason.VALIDATION:
        return error_text(value)
    if value.reason in _GUIDANCE and value.reason != ErrorReason.UNKNOWN:
        return t(_GUIDANCE[value.reason])
    return t(MessageKey.OPERATION_FAILED)


_CODE_GUIDANCE = {
    LoopReview.CONTINUATION_CODE: MessageKey.FAILURE_COMPLETION_REVIEW_LIMIT,
    LoopReview.ATTEMPTS_CODE: MessageKey.FAILURE_COMPLETION_REVIEW_LIMIT,
    CompletionErrorCode.INVALID_RESPONSE: MessageKey.FAILURE_COMPLETION_INVALID,
    ToolErrorCode.INVOCATION_INVALID: MessageKey.FAILURE_TOOL_ARGUMENTS,
    RunErrorCode.TOOL_CONTRACT: MessageKey.FAILURE_TOOL_CONTRACT,
    RunErrorCode.SANDBOX_UNAVAILABLE: MessageKey.FAILURE_SANDBOX,
    RunErrorCode.OPERATION_UNCERTAIN: MessageKey.FAILURE_OPERATION_UNCERTAIN,
    RunErrorCode.OPERATION_CONFLICT: MessageKey.FAILURE_OPERATION_CONFLICT,
    ToolErrorCode.FAILED: MessageKey.FAILURE_TOOL_FAILED,
    StepErrorCode.TIMEOUT: MessageKey.FAILURE_STEP_TIMEOUT,
}


def failure_text(error: ErrorInfo, t) -> str:
    key = _GUIDANCE.get(error.reason, MessageKey.FAILURE_UNKNOWN)
    if error.code == CompletionErrorCode.INCOMPLETE:
        key = ErrorPresentation.INCOMPLETE_GUIDANCE.get(error.termination, key)
    elif error.code in _CODE_GUIDANCE:
        key = _CODE_GUIDANCE[error.code]
    text = t(key)
    evidence = [error.code] if error.code else []
    if error.http_status is not ErrorInfo.NO_HTTP_STATUS:
        evidence.append(ErrorPresentation.HTTP_STATUS.format(status=error.http_status))
    if error.exception_type:
        evidence.append(error.exception_type)
    if error.raw_finish_reason:
        evidence.append(
            ErrorPresentation.FINISH_REASON.format(reason=error.raw_finish_reason)
        )
    if evidence:
        text += ErrorPresentation.PARAGRAPH + t(
            MessageKey.FAILURE_EVIDENCE,
            evidence=ErrorPresentation.EVIDENCE_SEPARATOR.join(evidence),
        )
    for title, value in (
        (MessageKey.FAILURE_SERVER_DETAIL, error.detail),
        (MessageKey.FAILURE_MODEL_ANALYSIS, error.explanation),
    ):
        if value:
            # Evidence/model text stays literal Markdown code, never executable XML.
            text += (
                ErrorPresentation.PARAGRAPH
                + t(title)
                + ErrorPresentation.NEWLINE
                + ErrorPresentation.NEWLINE.join(
                    ErrorPresentation.LITERAL_INDENT + line
                    for line in plain(value).splitlines()
                )
            )
    if error.recovery in _OUTCOMES:
        text += ErrorPresentation.PARAGRAPH + t(
            MessagePattern.recovery_result(error.recovery)
        )
    return text + ErrorPresentation.NEWLINE + t(MessageKey.FAILURE_DETAILS)
