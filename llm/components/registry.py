"""Component 이름, 담당 디렉토리와 capability를 등록한다. 필요한 capability만 해석하며 개별 기능의 내부 데이터 형식은 알지 못한다.

Resolve selected component identities without owning their path layouts."""

import re
from copy import deepcopy
from typing import Any

from llm.core.models import Project
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import prepare_create
from .base import ProjectComponent, validate_name
from llm.errors import CodedError


class ComponentDependencyError(CodedError, ValueError):
    """Application이 문자열 파싱 없이 미선택/미등록 의존성을 표시할 수 있다."""

    code = "component_dependency_missing"

    def __init__(self, missing, unavailable):
        self.missing = deepcopy(missing)
        self.unavailable = tuple(unavailable)
        super().__init__(f"Missing required components: {missing}; unavailable: {list(unavailable)}")


class ComponentRegistry:
    def __init__(self, components: tuple[ProjectComponent, ...] = ()) -> None:
        self._components: dict[str, ProjectComponent] = {}
        for component in components:
            self.register(component)

    def register(self, component: ProjectComponent) -> None:
        describe = getattr(component, "describe_config", None)
        if callable(describe):
            from llm.core.schema import checked_implementation_schema
            checked_implementation_schema(describe())
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
        required = getattr(component, "required_components", ())
        if (not isinstance(required, tuple)
                or any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in required)
                or len(set(required)) != len(required) or component.name in required):
            raise ValueError("required_components must be a tuple of distinct other Component identities")
        self._components[component.name] = component

    def get(self, name: str) -> ProjectComponent:
        try:
            return self._components[name]
        except KeyError:
            raise ValueError("Unavailable Project component") from None

    def names(self) -> tuple[str, ...]:
        """등록 순서대로 사용 가능한 Component 이름을 반환한다."""
        return tuple(self._components)

    def dependents(self, project, name, identifier=None, *, path=None):
        """비활성 Component의 보존된 정의도 삭제 보호에 포함한다."""
        self.stored(project)
        result = []
        for owner, component in self._components.items():
            reader = getattr(component, "references", None)
            if reader is None:
                continue
            for ref in reader(deepcopy(project)):
                if (ref["component"] == name and (identifier is None or ref["identifier"] == identifier)
                        and (path is None or ref.get("path") in (None, path))):
                    result.append({"component": owner, "identifier": ref["source"]})
        return result

    def require_unreferenced(self, project, name, identifier=None, *, path=None):
        refs = self.dependents(project, name, identifier, path=path)
        if refs:
            raise ValueError(f"Resource is referenced; remove references first: {refs}")

    def history_references(self, project):
        """Direct deletion and retention share the stored owners' history references."""
        self.stored(project)
        refs = {"session_ids": set(), "run_ids": set(), "message_ids": set()}
        for component in self._components.values():
            reader = getattr(component, "history_references", None)
            if reader:
                for kind, values in reader(deepcopy(project)).items():
                    if kind in refs:
                        refs[kind].update(values)
        return refs

    def validate_selection(self, project, names):
        """남길 소비자가 참조하는 영속 Component를 선택 해제하지 않는다."""
        for name in set(project.components) - set(names):
            refs = [ref for ref in self.dependents(project, name) if ref["component"] in names]
            if refs:
                raise ValueError(f"Component is referenced by selected components: {refs}")

    def validate(self, names: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(names, (tuple, list)) or any(not isinstance(name, str) for name in names):
            raise ValueError("Component selection must be a sequence of identities")
        if len(set(names)) != len(names) or any(name not in self._components for name in names):
            raise ValueError("Duplicate or unavailable Project component")
        missing = {name: [dependency for dependency in getattr(self._components[name], "required_components", ())
                          if dependency not in names] for name in names}
        missing = {name: dependencies for name, dependencies in missing.items() if dependencies}
        if missing:
            unavailable = sorted({name for dependencies in missing.values() for name in dependencies
                                  if name not in self._components})
            raise ComponentDependencyError(missing, unavailable)
        return tuple(names)

    def stored(self, project: Project) -> tuple[str, ...]:
        """Unavailable retained owners cannot disappear from usage or backup."""
        for name in project.initialized_components:
            self.get(name)
        return project.initialized_components

    def initialize(self, project: Project) -> None:
        self.stored(project)
        self.validate_config(project)
        for name in self.validate(project.components):
            root = project.paths.root / self._components[name].directory
            if not root.exists():
                prepare_create(root)
            self._components[name].initialize(deepcopy(project))
            if name not in project.initialized_components:
                project.initialized_components = (*project.initialized_components, name)
        log_event(project.paths.logs, "components.initialized", entity_id=project.id,
                  count=len(project.components))

    def validate_config(self, project: Project) -> None:
        """등록된 컴포넌트에만 Project 설정을 전달한다. 비활성 자료는 생성하지 않는다."""
        project.config.validate()
        self.validate(project.components)
        for name in project.config.parameters.get("components", {}):
            component = self.get(name)
            validate = getattr(component, "validate_project_config", None)
            if validate is None:
                raise ValueError(f"Component {name} does not support ProjectConfig parameters")
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
            self._components[name].get_config(deepcopy(source))
            self._components[name].clone(deepcopy(source), deepcopy(destination))
