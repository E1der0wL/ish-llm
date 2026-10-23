"""Project-owned Python Tool 패키지와 활성화 선택. 실행 수명은 기존 ToolExecutor가 소유한다."""

from llm.core.implementation_parameters import ImplementationParameters

from copy import deepcopy
from typing import Sequence
from llm.components.base import Component, validate_name
from llm.core.parameters import ParameterLayout
from .registry import ToolRegistry
from .data import ToolData
from .packages import ToolPaths, read_package, write_package, delete_package, load_tool


class ToolComponent(Component):
    name = ToolPaths.COMPONENT
    directory = ToolPaths.COMPONENT
    capabilities = ("tools",)
    data_class = ToolData
    parameter_layout = ParameterLayout(config=("enabled",))

    @staticmethod
    def _selection_names(names: Sequence[str]) -> list[str]:
        if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
            raise ValueError("Tool names must be a sequence")
        return [validate_name(name) for name in names]

    def describe_config(self):
        from llm.core.schema import object_schema
        return self.parameter_layout.schema(object_schema({"enabled": {"type": "array", "items": {"type": "string"},
            "uniqueItems": True, "description": "Run에 노출할 Project Python Tool 이름"}}))

    def validate_config(self, configuration):
        super().validate_config(configuration)
        names = ImplementationParameters.read(configuration).config.get("enabled", [])
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("Enabled tools must be a list of names")
        if len(set(names)) != len(names):
            raise ValueError("Duplicate enabled tool")

    def get_config(self, project):
        data = deepcopy(project.config.parameter_targets.component(self.name).to_dict())
        self.serialize(data)
        self.validate_config(data)
        return data

    def initialize(self, project):
        self.get_config(project)
        created = self.name not in project.initialized_components
        self._initialize_storage(project, records=False, record_catalog=False)
        paths = ToolPaths(self.root(project), project.id)
        if created:
            paths.initialize()
        else:
            paths.inventory()

    def enabled(self, project):
        return self._options(project).get("enabled", [])

    def create(self, project, data, *, identifier=None):
        paths = ToolPaths.for_project(project)
        validate_name(identifier)
        if paths.package(identifier).exists():
            raise FileExistsError("Tool package already exists")
        write_package(paths, identifier, data)
        return identifier

    def load(self, project, identifier):
        return read_package(ToolPaths.for_project(project), identifier)

    def list(self, project):
        if not self._has_storage(project):
            return {}
        return {name: value.to_dict() for name, value in ToolPaths.for_project(project).inventory()[1].items()}

    def save(self, project, identifier, data):
        self.load(project, identifier)
        write_package(ToolPaths.for_project(project), identifier, data)

    def delete(self, project, identifier):
        if identifier in self.enabled(project):
            raise ValueError("Disable the tool before deleting its package")
        self.load(project, identifier)
        paths = ToolPaths.for_project(project)
        delete_package(paths, identifier)

    def prepare(self, project, identifier):
        value = load_tool(project, identifier, prepare=True)
        return ToolRegistry((value,)).definitions()[0]

    def resolve_tools(self, project):
        return ToolRegistry(tuple(load_tool(project, name) for name in self.enabled(project)))

    def resolve(self, project, capability):
        if capability != "tools":
            return super().resolve(project, capability)
        return self.resolve_tools(project)
