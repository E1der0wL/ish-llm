"""Project-owned Python Tool 패키지와 활성화 선택. 실행 수명은 기존 ToolExecutor가 소유한다."""

from copy import deepcopy
from typing import Sequence
from llm.components.base import Component, validate_name
from llm.core.settings import SettingsLayout
from llm.services.infrastructure.storage import make_directory, remove_named_tree
from .registry import ToolRegistry
from .data import ToolData
from .packages import ToolPaths, read_package, write_package, load_tool


class ToolComponent(Component):
    name = "tools"
    directory = "tools"
    capabilities = ("tools",)
    data_class = ToolData
    settings_layout = SettingsLayout(config=("enabled",))

    @staticmethod
    def _selection_names(names: Sequence[str]) -> list[str]:
        if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
            raise ValueError("Tool names must be a sequence")
        return [validate_name(name) for name in names]

    def configuration_schema(self):
        from llm.core.schema import object_schema
        return self.settings_layout.schema(object_schema({"enabled": {"type": "array", "items": {"type": "string"},
            "uniqueItems": True, "description": "Run에 노출할 Project Python Tool 이름"}}))

    def validate_configuration(self, configuration):
        super().validate_configuration(configuration)
        names = configuration.get("config", {}).get("enabled", [])
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("Enabled tools must be a list of names")
        if len(set(names)) != len(names):
            raise ValueError("Duplicate enabled tool")

    def configuration(self, project):
        data = deepcopy(project.config.parameters.get("components", {}).get(self.name, {}))
        self.serialize(data)
        self.validate_configuration(data)
        return data

    def initialize(self, project):
        self.configuration(project)
        make_directory(ToolPaths.for_project(project).root)

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
        root = ToolPaths.for_project(project).root
        return {path.name: self.load(project, path.name) for path in sorted(root.iterdir())}

    def save(self, project, identifier, data):
        self.load(project, identifier)
        write_package(ToolPaths.for_project(project), identifier, data)

    def delete(self, project, identifier):
        if identifier in self.enabled(project):
            raise ValueError("Disable the tool before deleting its package")
        self.load(project, identifier)
        paths = ToolPaths.for_project(project)
        remove_named_tree(paths.root, paths.package(identifier), identifier)

    def prepare(self, project, identifier):
        value = load_tool(project, identifier, prepare=True)
        return ToolRegistry((value,)).definitions()[0]

    def resolve_tools(self, project):
        return ToolRegistry(tuple(load_tool(project, name) for name in self.enabled(project)))

    def resolve(self, project, capability):
        if capability != "tools":
            return super().resolve(project, capability)
        return self.resolve_tools(project)
