"""선언된 capability를 Run별로 구성한다. Tool 제공자는 하나의 ToolRegistry로 합치고 다른 capability는 제공자별 튜플로 전달한다.

Adapt generic component exports to one Run's tool snapshot."""

from typing import Protocol
from llm.core.models import Project
from llm.components.registry import ComponentRegistry
from llm.components.processing import ordered_processors
from .registry import ToolRegistry


class CapabilityResolver(Protocol):
    def resolve(self, project: Project, names: tuple[str, ...]) -> dict: ...


class ComponentToolResolver:
    def __init__(self, components: ComponentRegistry, *, data_factory=None) -> None:
        self.components = components
        self.data_factory = data_factory

    def resolve_tools(self, project: Project) -> ToolRegistry:
        """전달된 Project 사본의 설정을 사용한다. 서비스 호출자는 최신 Project를 전달한다."""
        tools = ToolRegistry()
        for exported in self.components.resolve(project, "tools", data_factory=self.data_factory):
            if not isinstance(exported, ToolRegistry):
                raise TypeError("Tool capability must export a ToolRegistry")
            tools.extend(exported)
        return tools

    def resolve(self, project: Project, names: tuple[str, ...]) -> dict:
        """명시적으로 요청한 기능만 구성한다. 일반 기능은 제공자별 값의 튜플이다."""
        values = {}
        for name in names:
            if name == "tools":
                values[name] = self.resolve_tools(project)
            else:
                exports = self.components.resolve(project, name, data_factory=self.data_factory)
                # 처리기 컬렉션은 제공자가 없어도 유효하다. 개별 도메인 이름을 알지 않는다.
                if name == "completion_processors":
                    exports = ordered_processors(exports)
                elif not exports:
                    raise ValueError(f"Required capability unavailable: {name}")
                values[name] = exports
        return values
