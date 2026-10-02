"""Workflow 그래프 정의를 열린 JSON 데이터로 관리한다. 그래프 실행과 Step 생성은 이 저장 클래스가 아닌 Engine의 책임이다.

Open workflow graph definitions; execution belongs to Graph engines."""

from pathlib import Path
from dataclasses import dataclass
from llm.core.models import Project
from llm.components.definitions import DefinitionComponent
from .data import WorkflowData
from .graph import validate_graph


@dataclass(frozen=True, slots=True)
class WorkflowPaths:
    root: Path

    @classmethod
    def for_project(cls, project: Project) -> "WorkflowPaths":
        return cls(project.paths.root / WorkflowComponent.directory)


class WorkflowComponent(DefinitionComponent):
    """버전 1 그래프만 저장·조회한다. 확장 필드는 검증 후에도 보존한다."""

    name = "workflows"
    directory = "workflows"
    capabilities = ("workflows",)
    data_class = WorkflowData

    def validate_record(self, identifier: str, data: dict) -> None:
        validate_graph(data)
