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

    @workspace_locked
    def inspect(self, definition: dict, *, handlers, declared_tools=None):
        """Inspect a draft against selected declarations, without runtime preparation.

        Host Tools can be supplied as a declaration registry. Saved Project Tools
        are read as records only; their Python source is never imported here.
        """
        from llm.core.agents import AgentDefinition
        from llm.components.tools import ToolComponent
        from .inspection import inspect_graph, InspectedDefinition
        from llm.core.agent_views import AgentSnapshot
        from llm.core.editable_snapshot import EditableSnapshot

        project, _ = self._current()

        def read(target):
            if target.component == AgentDefinition.REFERENCE_COMPONENT:
                snapshot = AgentSnapshot.read(self.agents().snapshot(target.identifier))
                return InspectedDefinition(snapshot.definition, snapshot.revision)
            if target.component == ToolComponent.name and declared_tools is not None:
                if target.identifier in declared_tools.names():
                    tool = declared_tools.get(target.identifier)
                    return InspectedDefinition(tool.parameters, tool.contract.revision)
            if target.component not in project.components:
                raise ValueError(f'Component is not selected: {target.component}')
            snapshot = EditableSnapshot.read(self.related(target.component).snapshot(target.identifier))
            return InspectedDefinition(snapshot.data, snapshot.version)

        return inspect_graph(definition, handlers=handlers, read_reference=read)

    avalidate = async_method(validate)
    agraph = async_method(graph)
    ainspect = async_method(inspect)
