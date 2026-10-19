"""닫힌 Workflow 구조를 검증·저장한다. 실행과 handler별 설정 검증은 GraphEngine의 책임이다."""

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

    def references(self, project):
        """Saved nested Agent nodes retain their Project-owned definition IDs."""
        def agents(graph):
            for node in graph["nodes"].values():
                if node["type"] == "agent" and isinstance(node.get("agent"), str):
                    yield node["agent"]
                elif node["type"] == "loop":
                    yield from agents(node["body"])
        return [{"component": "agents", "identifier": agent, "source": identifier}
                for identifier, value in self.list(project).items() for agent in sorted(set(agents(value)))]
