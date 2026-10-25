"""Display-only projection of Graph-owned Step evidence; no execution authority."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar
from llm.core.models import Step, StepStatus
from llm.engines.graph.node_records import GraphStepMetadata
from llm.engines.graph.runtime_values import GraphExecutionObservation, GraphScope
from llm.components.workflows.contracts import WorkflowDocument
from llm.components.workflows.graph import WorkflowGraph


@dataclass(frozen=True, slots=True)
class WorkflowObservation:
    """Hub's single read adapter for Graph's declared observation fields.

    Graph currently provides metadata declarations, not a complete read
    projection. Definition validation delegates to WorkflowGraph. This UI
    projection never reconstructs missing evidence or grants execution rights.
    """

    INVALID_EVIDENCE: ClassVar[str] = "Invalid Graph Step observation: {field}"
    ENSURE_ASCII: ClassVar[bool] = False
    EMPTY: ClassVar[str] = ""
    text: str = EMPTY
    states: tuple[tuple[str, str], ...] = ()

    @classmethod
    def _required(cls, step, field, expected):
        value = step.metadata[field]
        if not isinstance(value, expected) or (expected is str and not value):
            raise ValueError(cls.INVALID_EVIDENCE.format(field=field))
        return value

    @classmethod
    def read(cls, steps: Sequence[Step]) -> "WorkflowObservation":
        root = next((step for step in steps
                     if step.kind == GraphExecutionObservation.ROOT_STEP), None)
        if root is None:
            return cls()
        graph = cls._required(root, GraphStepMetadata.DEFINITION, dict)
        WorkflowGraph.from_dict(graph)
        workflow_id = cls._required(root, GraphStepMetadata.WORKFLOW_ID, str)
        states = {}
        for step in steps:
            if step.kind != GraphExecutionObservation.NODE_STEP:
                continue
            node = cls._required(step, GraphStepMetadata.NODE_ID, str)
            path = cls._required(step, GraphStepMetadata.PATH, str)
            if path == GraphScope.node_path(workflow_id, node):
                if node not in graph[WorkflowDocument.NODES]:
                    raise ValueError(cls.INVALID_EVIDENCE.format(field=GraphStepMetadata.NODE_ID))
                states[node] = StepStatus(step.status).value
        return cls(json.dumps(graph, ensure_ascii=cls.ENSURE_ASCII), tuple(states.items()))
