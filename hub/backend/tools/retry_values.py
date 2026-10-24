"""Hub Tool repair annotations and model reply boundaries; Backend admission remains authoritative."""

from typing import NamedTuple
from llm.core.step_event_metadata import StepEventMetadata
from llm.core.step_tool_metadata import ToolStepMetadata
from llm.policies.retry import RetryDecision


class ToolRepairPhase:
    REPAIRING = "repairing"


class ToolRepairMetadata:
    REPAIR = "hub_tool_repair"
    TOOL = "tool"
    SELECTED = True

    @classmethod
    def create(cls, parent_step_id, tool, attempt):
        return {
            cls.REPAIR: cls.SELECTED,
            StepEventMetadata.PARENT_STEP_ID: parent_step_id,
            cls.TOOL: tool,
            ToolStepMetadata.RETRY_ATTEMPT: attempt,
        }

    @classmethod
    def progress(cls, attempt):
        return ToolStepMetadata.retry_progress(ToolRepairPhase.REPAIRING, attempt)


class ToolModelReply:
    NO_DECISION = None

    @classmethod
    def read(cls, value):
        key = RetryDecision.changes.__name__
        if not isinstance(value, dict) or set(value) != {key}:
            return cls.NO_DECISION
        return (
            RetryDecision(value[key])
            if isinstance(value[key], dict)
            else cls.NO_DECISION
        )


class FailedInvocation(NamedTuple):
    tool: object
    call: object
    cause: Exception
