"""Project execution observations without presenting checkpoint reuse as work."""

from hashlib import sha256
import json

from llm.core.models import Step, StepKind, StepStatus
from llm.core.step_event_metadata import StepEventMetadata
from llm.core.step_tool_metadata import ToolStepMetadata
from llm.engines.graph.node_records import GraphStepMetadata
from llm.engines.loop.records import LoopStepMetadata
from llm.services.runtime.tools import ToolExecutionPhase
from ..tools.retry_values import ToolRepairPhase
from ...model import StepSummary


class StepDisplay:
    NO_ID = ""
    NO_TIME = ""
    ABSENT = None
    TOOL_PREFIX = "tool:"
    ENSURE_ASCII = False
    RETRY_PHASES = (ToolRepairPhase.REPAIRING, ToolExecutionPhase.RETRYING)

    @classmethod
    def identity(cls, step, metadata, node_key, call_id):
        loop = LoopStepMetadata(metadata)
        key = [step.name, loop.checkpoint_name, node_key, loop.iteration, call_id]
        return (
            cls.TOOL_PREFIX
            + sha256(
                json.dumps(key, ensure_ascii=cls.ENSURE_ASCII).encode()
            ).hexdigest()
        )


def display_steps(steps) -> tuple[StepSummary, ...]:
    result = []
    for step in steps:
        metadata = getattr(step, Step.metadata.__name__, {})
        graph = GraphStepMetadata(metadata)
        if StepEventMetadata.was_reused(metadata) or graph.reused_step_id:
            continue
        kind, status = str(step.kind), str(step.status)
        identity = getattr(step, Step.id.__name__, StepDisplay.NO_ID)
        if kind == StepKind.TOOL:
            call_id = ToolStepMetadata.call_id(metadata)
            node_key = graph.node_checkpoint_key
            if call_id or node_key:
                identity = StepDisplay.identity(step, metadata, node_key, call_id)
            if (
                status == StepStatus.CANCELLED
                and StepEventMetadata.observed_phase(metadata)
                == ToolExecutionPhase.WAITING_APPROVAL
            ):
                status = StepSummary.WAITING_APPROVAL
            elif (
                status == StepStatus.RUNNING
                and StepEventMetadata.observed_phase(metadata)
                in StepDisplay.RETRY_PHASES
            ):
                status = StepSummary.RETRYING
        result.append(
            StepSummary(
                step.name,
                status,
                kind,
                identity,
                getattr(step, Step.started_at.__name__, StepDisplay.ABSENT)
                or StepDisplay.NO_TIME,
                getattr(step, Step.id.__name__, StepDisplay.NO_ID),
            )
        )
    return tuple(result)
