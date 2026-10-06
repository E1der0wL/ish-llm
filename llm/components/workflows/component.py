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
