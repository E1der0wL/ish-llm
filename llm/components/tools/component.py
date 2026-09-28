"""프로젝트별 Tool 정의와 활성화 목록을 관리한다. JSON 정의는 핸들러 없이 저장할 수 있고 실행 시에만 런타임 카탈로그와 결합한다.

Project-scoped tool selection; Python handlers remain application-owned."""

from pathlib import Path
from typing import Optional, Sequence
from llm.compat import dataclass
from llm.core.models import Project
from llm.components.base import Component
from .registry import Tool, ToolRegistry
from .data import ToolData


@dataclass(frozen=True, slots=True)
class ToolPaths:
    root: Path

    @classmethod
    def for_project(cls, project: Project) -> "ToolPaths":
        return cls(project.paths.root / ToolComponent.directory)



class ToolComponent(Component):
    name = "tools"
    directory = "tools"
    capabilities = ("tools",)
    data_class = ToolData

    def __init__(self, catalog: Optional[ToolRegistry] = None) -> None:
        self.catalog = catalog if catalog is not None else ToolRegistry()

    def default_configuration(self) -> dict:
        return {"enabled": []}

    def configuration_schema(self):
        from llm.core.schema import object_schema
        return object_schema({"enabled": {"type": "array", "items": {"type": "string"},
            "uniqueItems": True, "default": [], "description": "프로젝트에서 사용할 Tool 이름",
            "x-suggestions": list(self.catalog.names())}}, required=["enabled"], default=self.default_configuration())

    def validate_configuration(self, configuration: dict) -> None:
        if "enabled" not in configuration:
            raise ValueError("Tool configuration requires enabled names")
        names = configuration["enabled"]
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("Enabled tools must be a list of names")
        if len(set(names)) != len(names):
            raise ValueError("Duplicate enabled tool")

    @staticmethod
    def _selection_names(names: Sequence[str]) -> list[str]:
        if (isinstance(names, (str, bytes)) or not isinstance(names, Sequence)
                or any(not isinstance(name, str) or not name.strip() for name in names)):
            raise ValueError("Tool names must be a sequence of nonempty strings")
        return list(names)

    def enabled(self, project: Project) -> list[str]:
        """Read selected names without resolving definitions or Python handlers."""
        return self.configuration(project)["enabled"]

    @staticmethod
    async def _unbound(arguments):
        raise RuntimeError("Tool definition has no runtime handler")

    def _definition(self, identifier: str, data: dict) -> Tool:
        function = data.get("function")
        if (data.get("type") != "function" or not isinstance(function, dict)
                or function.get("name") != identifier
                or not isinstance(function.get("parameters"), dict)
                or not isinstance(function.get("description", ""), str)):
            raise ValueError("Expected a named function tool definition")
        tool = Tool(identifier, function.get("description", ""), function["parameters"], self._unbound, data)
        ToolRegistry((tool,))  # validate schema and native definition together
        return tool

    def validate_record(self, identifier: str, data: dict) -> None:
        self._definition(identifier, data)

    def create(self, project: Project, data: dict, *, identifier: Optional[str] = None) -> str:
        if identifier is None and isinstance(data, dict) and isinstance(data.get("function"), dict):
            identifier = data["function"].get("name")
        return super().create(project, data, identifier=identifier)

    def delete(self, project: Project, identifier: str) -> None:
        if identifier in self.configuration(project)["enabled"]:
            raise ValueError("Disable the tool before deleting its definition")
        super().delete(project, identifier)

    def resolve_tools(self, project: Project) -> ToolRegistry:
        names = self.configuration(project)["enabled"]
        tools = ToolRegistry()
        for name in names:
            try:
                data = self.load(project, name)
            except FileNotFoundError:
                # 런타임 카탈로그는 기본 정의를, Project 레코드는 정의 재정의를 제공한다.
                tool = self.catalog.get(name)
            else:
                definition = self._definition(name, data)
                tool = Tool(name, definition.description, definition.parameters,
                            self.catalog.get(name).handler, definition.definition, self.catalog.get(name).contract)
            tools.register(tool)
        return tools

    def resolve(self, project: Project, capability: str):
        if capability != "tools":
            return super().resolve(project, capability)
        return self.resolve_tools(project)
