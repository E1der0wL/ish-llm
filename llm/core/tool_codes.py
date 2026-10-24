"""Tool boundary failure vocabulary; no execution or storage dependencies."""

from enum import StrEnum


class ToolErrorCode(StrEnum):
    FAILED = "tool_failed"
    SCOPE_CLOSED = "tool_scope_closed"
    INTERRUPTED = "tool_interrupted"
    INVOCATION_INVALID = "tool_invocation_invalid"
    USAGE_CORRUPT = "tool_usage_corrupt"
    APPROVAL_REQUIRED = "tool_approval_required"
    WORKER_PROTOCOL = "tool_worker_protocol"
    WORKER_FAILED = "tool_worker_failed"
    WORKER_OUTPUT_LIMIT = "tool_worker_output_limit"
