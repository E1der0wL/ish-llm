"""Component 이름, 담당 디렉토리와 capability를 등록한다. 필요한 capability만 해석하며 개별 기능의 내부 데이터 형식은 알지 못한다.

Resolve selected component identities without owning their path layouts."""

from llm.core.history_references import HistoryReferenceSet

from llm.core.resource_references import ResourceTarget, StoredResourceReference

from copy import deepcopy
from typing import Any

from llm.core.models import Project
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import prepare_create
from .base import ProjectComponent, Component, ComponentHook, validate_name
from .naming import ComponentIdentity
from llm.errors import CodedError


class ComponentDependencyError(CodedError, ValueError):
    """Application이 문자열 파싱 없이 미선택/미등록 의존성을 표시할 수 있다."""

    code = "component_dependency_missing"
    MESSAGE = "Missing required components: {missing}; unavailable: {unavailable}"

    def __init__(self, missing, unavailable):
        self.missing = deepcopy(missing)
        self.unavailable = tuple(unavailable)
        super().__init__(self.MESSAGE.format(missing=missing, unavailable=list(unavailable)))


class ComponentRegistry:
    ABSENT = None
    NO_COMPONENTS = ()
    INITIALIZED_EVENT = "components.initialized"
    REFERENCED_RESOURCE = "Resource is referenced; remove references first: {refs}"
    REFERENCED_COMPONENT = "Component is referenced by selected components: {refs}"
    MISSING_CONFIGURATION_HOOK = "Component {name} does not support ProjectConfig parameters"
    INVALID_DATA_CLASS = "Component data_class must inherit ComponentData"
    INVALID_IDENTITY = "Invalid or duplicate component identity"
    RESERVED_DIRECTORY = "Component directory is reserved or already owned"
    INVALID_CAPABILITIES = "Capabilities must be a tuple of distinct names"
    INVALID_DEPENDENCIES = "required_components must be a tuple of distinct other Component identities"
    UNAVAILABLE = "Unavailable Project component"
    INVALID_SELECTION = "Component selection must be a sequence of identities"
    UNAVAILABLE_SELECTION = "Duplicate or unavailable Project component"
    NOT_ENABLED = "Component is not enabled for this Project"

    @staticmethod
    def require_enabled(project, name):
        if name not in project.components:
            raise ValueError(ComponentRegistry.NOT_ENABLED)

    def __init__(self, components: tuple[ProjectComponent, ...] = NO_COMPONENTS) -> None:
        self._components: dict[str, ProjectComponent] = {}
        for component in components:
            self.register(component)

    def register(self, component: ProjectComponent) -> None:
        describe = getattr(component, ComponentHook.DESCRIBE_CONFIG, self.ABSENT)
        if callable(describe):
            from llm.core.schema import checked_implementation_schema

            checked_implementation_schema(describe())
        data_class = Component.data_type(component)
        if data_class is not self.ABSENT:
            from llm.services.lifecycle.components import ComponentData

            if not isinstance(data_class, type) or not issubclass(data_class, ComponentData):
                raise ValueError(self.INVALID_DATA_CLASS)
        if not ComponentIdentity.match(component.name) or component.name in self._components:
            raise ValueError(self.INVALID_IDENTITY)
        directory = validate_name(Component.declared_directory(component)).lower()
        if directory in ProjectPaths.CORE_DIRECTORIES or any(
            item.directory.lower() == directory for item in self._components.values()
        ):
            raise ValueError(self.RESERVED_DIRECTORY)
        capabilities = component.capabilities
        if (
            not isinstance(capabilities, tuple)
            or any(not isinstance(name, str) or not name for name in capabilities)
            or len(set(capabilities)) != len(capabilities)
        ):
            raise ValueError(self.INVALID_CAPABILITIES)
        required = Component.declared_dependencies(component)
        if (
            not isinstance(required, tuple)
            or any(not isinstance(name, str) or not ComponentIdentity.match(name) for name in required)
            or len(set(required)) != len(required)
            or component.name in required
        ):
            raise ValueError(self.INVALID_DEPENDENCIES)
        self._components[component.name] = component

    def get(self, name: str) -> ProjectComponent:
        try:
            return self._components[name]
        except KeyError:
            raise ValueError(self.UNAVAILABLE) from None

    def names(self) -> tuple[str, ...]:
        """등록 순서대로 사용 가능한 Component 이름을 반환한다."""
        return tuple(self._components)

    def dependents(self, project, name, identifier=ABSENT, *, path=ABSENT):
        """비활성 Component의 보존된 정의도 삭제 보호에 포함한다."""
        self.stored(project)
        result = []
        for owner, component in self._components.items():
            reader = getattr(component, ComponentHook.REFERENCES, self.ABSENT)
            if reader is self.ABSENT:
                continue
            for value in reader(deepcopy(project)):
                reference = StoredResourceReference.read(value)
                if reference.matches(name, identifier, path=path):
                    result.append(reference.dependent(owner).to_dict())
        return result

    def require_unreferenced(self, project, name, identifier=ABSENT, *, path=ABSENT):
        refs = self.dependents(project, name, identifier, path=path)
        if refs:
            raise ValueError(self.REFERENCED_RESOURCE.format(refs=refs))

    def history_references(self, project):
        """Direct deletion and retention share the stored owners' history references."""
        self.stored(project)
        refs = HistoryReferenceSet.empty()
        for component in self._components.values():
            reader = HistoryReferenceSet.reader(component)
            if reader:
                refs.merge(reader(deepcopy(project)))
        return refs.to_dict()

    def validate_selection(self, project, names):
        """남길 소비자가 참조하는 영속 Component를 선택 해제하지 않는다."""
        for name in set(project.components) - set(names):
            refs = [
                ref
                for ref in self.dependents(project, name)
                if ResourceTarget.from_dict(ref).component in names
            ]
            if refs:
                raise ValueError(self.REFERENCED_COMPONENT.format(refs=refs))

    def validate(self, names: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(names, (tuple, list)) or any(not isinstance(name, str) for name in names):
            raise ValueError(self.INVALID_SELECTION)
        if len(set(names)) != len(names) or any(name not in self._components for name in names):
            raise ValueError(self.UNAVAILABLE_SELECTION)
        missing = {
            name: [
                dependency
                for dependency in Component.declared_dependencies(self._components[name])
                if dependency not in names
            ]
            for name in names
        }
        missing = {name: dependencies for name, dependencies in missing.items() if dependencies}
        if missing:
            unavailable = sorted(
                {
                    name
                    for dependencies in missing.values()
                    for name in dependencies
                    if name not in self._components
                }
            )
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
        log_event(
            project.paths.logs, self.INITIALIZED_EVENT, entity_id=project.id, count=len(project.components)
        )

    def validate_config(self, project: Project) -> None:
        """등록된 컴포넌트에만 Project 설정을 전달한다. 비활성 자료는 생성하지 않는다."""
        project.config.validate()
        self.validate(project.components)
        for name in project.config.parameter_targets.component_names:
            component = self.get(name)
            validate = getattr(component, ComponentHook.VALIDATE_PROJECT_CONFIG, self.ABSENT)
            if validate is self.ABSENT:
                raise ValueError(self.MISSING_CONFIGURATION_HOOK.format(name=name))
            validate(deepcopy(project))

    def resolve(self, project: Project, capability: str, *, data_factory=ABSENT) -> tuple[Any, ...]:
        """Collect optional exports without knowing their domain or value types."""
        values = []
        for name in self.validate(project.components):
            component = self._components[name]
            if capability in component.capabilities:
                runtime = getattr(component, ComponentHook.RESOLVE_RUNTIME, self.ABSENT)
                if data_factory is not self.ABSENT and runtime is not self.ABSENT:
                    values.append(
                        runtime(
                            deepcopy(project),
                            capability,
                            data_factory=lambda name: data_factory(project, name),
                        )
                    )
                else:
                    values.append(component.resolve(deepcopy(project), capability))
        return tuple(values)

    def clone(self, source: Project, destination: Project) -> None:
        for name in self.validate(source.components):
            self._components[name].get_config(deepcopy(source))
            self._components[name].clone(deepcopy(source), deepcopy(destination))
