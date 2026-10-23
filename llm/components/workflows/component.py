"""닫힌 Workflow 구조를 검증·저장한다. 실행과 handler별 설정 검증은 GraphEngine의 책임이다."""

from llm.core.resource_references import AgentUse, AgentReferenceReader

from llm.components.workflows.graph import ControlNodeType

from pathlib import Path
from dataclasses import dataclass
from llm.core.models import Project
from llm.components.definitions import DefinitionComponent
from .data import WorkflowData
from .graph import validate_graph, workflow_schema


@dataclass(frozen=True, slots=True)
class WorkflowPaths:
    root: Path

    @classmethod
    def for_project(cls, project: Project) -> "WorkflowPaths":
        return cls(project.paths.root / WorkflowComponent.directory)


class WorkflowComponent(DefinitionComponent):
    """버전 1 그래프를 저장·조회한다. Application 확장은 metadata에 둔다."""

    name = "workflows"
    directory = "workflows"
    capabilities = ("workflows",)
    data_class = WorkflowData
    schema = workflow_schema()

    def validate_record(self, identifier: str, data: dict) -> None:
        validate_graph(data)

    def agent_references(self, project, engines):
        """Read retained actions through their registered, effect-free handler owners."""
        handlers = {}
        for name in engines.names():
            declare = getattr(engines.get(name), "workflow_handlers", None)
            if declare is not None:
                for kind, handler in declare().items():
                    handlers.setdefault(kind, []).append(handler)

        def selected(graph):
            for node in graph["nodes"].values():
                kind = node["type"]
                if kind == ControlNodeType.LOOP:
                    yield from selected(node["body"])
                elif kind not in (ControlNodeType.BRANCH, ControlNodeType.PARALLEL, ControlNodeType.JOIN, ControlNodeType.END, ControlNodeType.WORKFLOW):
                    if kind not in handlers:
                        raise ValueError("Agent may be referenced; Workflow handler owner is unavailable")
                    for handler in handlers[kind]:
                        reader = AgentReferenceReader.from_owner(handler)
                        if reader is not None:
                            yield from reader(node)
        return [AgentUse(agent, identifier).to_dict()
                for identifier, graph in self.list(project).items() for agent in sorted(set(selected(graph)))]
