"""Workflow 데이터의 잠금·수명 검사를 유지하는 그래프 편의 API."""

from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method
from .graph import WorkflowGraph, validate_graph


class WorkflowData(ComponentData):
    """공통 CRUD 외에 버전 1 검증과 그래프 빌더 복원을 제공한다."""

    @workspace_locked
    def validate(self, identifier: str) -> None:
        project, component = self._current()
        validate_graph(component.load(project, identifier))

    @workspace_locked
    def graph(self, identifier: str) -> WorkflowGraph:
        """반환된 빌더를 수정한 뒤 save(id, graph.to_dict())로 명시적으로 저장한다."""
        project, component = self._current()
        return WorkflowGraph.from_dict(component.load(project, identifier))

    avalidate = async_method(validate)
    agraph = async_method(graph)
