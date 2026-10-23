"""Project-owned Python Tool 패키지와 활성화 선택. 실행 수명은 기존 ToolExecutor가 소유한다."""

from copy import deepcopy
from llm.components.base import Component, validate_name
from llm.core.parameters import ParameterLayout
from .registry import ToolRegistry
from .data import ToolData
from .selection import ToolSelection
from .packages import ToolPaths, read_package, write_package, delete_package, load_tool


class ToolComponent(Component):
    name = ToolPaths.COMPONENT
    directory = ToolPaths.COMPONENT
    capabilities = (ToolRegistry.CAPABILITY,)
    data_class = ToolData
    parameter_layout = ParameterLayout(config=(ToolSelection.enabled.__name__,))
    SELECTION_DESCRIPTION = "Run에 노출할 Project Python Tool 이름"
    DUPLICATE_PACKAGE = "Tool package already exists"
    ENABLED_PACKAGE = "Disable the tool before deleting its package"
    NO_IDENTIFIER = None
    DEFINITION_RECORDS = False
    RECORD_CATALOG = False
    PREPARE_DEPENDENCIES = True

    def describe_config(self):
        return self.parameter_layout.schema(ToolSelection.schema(description=self.SELECTION_DESCRIPTION))

    def get_config(self, project):
        data = deepcopy(project.config.parameter_targets.component(self.name).to_dict())
        self.serialize(data)
        self.validate_config(data)
        return data

    def initialize(self, project):
        self.get_config(project)
        created = self.name not in project.initialized_components
        self._initialize_storage(project, records=self.DEFINITION_RECORDS, record_catalog=self.RECORD_CATALOG)
        paths = ToolPaths(self.root(project), project.id)
        if created:
            paths.initialize()
        else:
            paths.inventory()

    def enabled(self, project):
        return ToolSelection.read(self._options(project), scope=self.name).names

    def create(self, project, data, *, identifier=NO_IDENTIFIER):
        paths = ToolPaths.for_project(project)
        validate_name(identifier)
        if paths.package(identifier).exists():
            raise FileExistsError(self.DUPLICATE_PACKAGE)
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
            raise ValueError(self.ENABLED_PACKAGE)
        self.load(project, identifier)
        paths = ToolPaths.for_project(project)
        delete_package(paths, identifier)

    def prepare(self, project, identifier):
        value = load_tool(project, identifier, prepare=self.PREPARE_DEPENDENCIES)
        return ToolRegistry((value,)).definitions()[0]

    def resolve_tools(self, project):
        return ToolRegistry(tuple(load_tool(project, name) for name in self.enabled(project)))

    def resolve(self, project, capability):
        if capability != ToolRegistry.CAPABILITY:
            return super().resolve(project, capability)
        return self.resolve_tools(project)
