"""Tool boundary failure vocabulary; no execution or storage dependencies."""

from enum import StrEnum


class ToolErrorCode(StrEnum):
    FAILED = "tool_failed"
    INVOCATION_INVALID = "tool_invocation_invalid"
    USAGE_CORRUPT = "tool_usage_corrupt"
    APPROVAL_REQUIRED = "tool_approval_required"
