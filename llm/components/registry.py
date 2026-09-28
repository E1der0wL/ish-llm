"""Component 이름, 담당 디렉토리와 capability를 등록한다. 필요한 capability만 해석하며 개별 기능의 내부 데이터 형식은 알지 못한다.

Resolve selected component identities without owning their path layouts."""

import re
from copy import deepcopy
from typing import Any

from llm.core.models import Project
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import prepare_create
from .base import ProjectComponent, validate_name


class ComponentRegistry:
    def __init__(self, components: tuple[ProjectComponent, ...] = ()) -> None:
        self._components: dict[str, ProjectComponent] = {}
        for component in components:
            self.register(component)

    def register(self, component: ProjectComponent) -> None:
        data_class = getattr(component, "data_class", None)
        if data_class is not None:
            from llm.services.lifecycle.components import ComponentData
            if not isinstance(data_class, type) or not issubclass(data_class, ComponentData):
                raise ValueError("Component data_class must inherit ComponentData")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", component.name) or component.name in self._components:
            raise ValueError("Invalid or duplicate component identity")
        directory = validate_name(getattr(component, "directory", None)).lower()
        if directory in {"sessions", "logs", "state", "cache"} or any(
                item.directory.lower() == directory for item in self._components.values()):
            raise ValueError("Component directory is reserved or already owned")
        capabilities = component.capabilities
        if (not isinstance(capabilities, tuple)
                or any(not isinstance(name, str) or not name for name in capabilities)
                or len(set(capabilities)) != len(capabilities)):
            raise ValueError("Capabilities must be a tuple of distinct names")
        self._components[component.name] = component

    def get(self, name: str) -> ProjectComponent:
        try:
            return self._components[name]
        except KeyError:
            raise ValueError("Unavailable Project component") from None

    def names(self) -> tuple[str, ...]:
        """등록 순서대로 사용 가능한 Component 이름을 반환한다."""
        return tuple(self._components)

    def validate(self, names: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(names, (tuple, list)) or any(not isinstance(name, str) for name in names):
            raise ValueError("Component selection must be a sequence of identities")
        if len(set(names)) != len(names) or any(name not in self._components for name in names):
            raise ValueError("Duplicate or unavailable Project component")
        return tuple(names)

    def initialize(self, project: Project) -> None:
        self.validate_configuration(project)
        for name in self.validate(project.components):
            root = project.paths.root / self._components[name].directory
            if not root.exists():
                prepare_create(root)
            self._components[name].initialize(deepcopy(project))
        log_event(project.paths.logs, "components.initialized", entity_id=project.id,
                  count=len(project.components))

    def validate_configuration(self, project: Project) -> None:
        """등록된 컴포넌트에만 Project 설정을 전달한다. 비활성 자료는 생성하지 않는다."""
        project.config.validate()
        for name in project.config.component_configurations:
            component = self.get(name)
            validate = getattr(component, "validate_project_configuration", None)
            if validate is None:
                raise ValueError(f"Component {name} does not support ProjectConfig settings")
            validate(deepcopy(project))

    def resolve(self, project: Project, capability: str, *, data_factory=None) -> tuple[Any, ...]:
        """Collect optional exports without knowing their domain or value types."""
        values = []
        for name in self.validate(project.components):
            component = self._components[name]
            if capability in component.capabilities:
                runtime = getattr(component, "resolve_runtime", None)
                if data_factory is not None and runtime is not None:
                    values.append(runtime(deepcopy(project), capability,
                                          data_factory=lambda name: data_factory(project, name)))
                else:
                    values.append(component.resolve(deepcopy(project), capability))
        return tuple(values)

    def clone(self, source: Project, destination: Project) -> None:
        for name in self.validate(source.components):
            self._components[name].configuration(deepcopy(source))
            self._components[name].clone(deepcopy(source), deepcopy(destination))
