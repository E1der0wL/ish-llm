"""Project의 생성, 저장, 복제, 삭제와 Component 초기화를 조율한다. 실제 하위 디렉토리 구조는 Task/Component에 위임한다."""

from llm.services.infrastructure.storage import read_domain_record, atomic_domain_json, revision_token, check_revision, recover_deletions

from llm.services.query import queryable
from typing import Iterable, Optional, Union
from copy import deepcopy
from contextlib import ExitStack
from llm.services.infrastructure.backups import DirectoryBackups
from pathlib import Path

from llm.core.models import Project, ProjectConfig, new_id
from llm.core.configuration import component_configuration
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.storage import atomic_json, child, read_json, record, remove_owned_tree
from llm.services.lifecycle.tasks import TaskManager
from llm.services.infrastructure.logging import log_event
from llm.services.lifecycle.access import ProjectAccess
from llm.services.results import RunResultQuery
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import WorkspaceOwnership, workspace_locked
from llm.components.registry import ComponentRegistry
from llm.components.base import ProjectComponent


# 프로젝트 JSON 저장과 경로/소유권 검사를 담당한다.
class ProjectRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.ownership = WorkspaceOwnership(root)

    def paths(self, project_id: str) -> ProjectPaths:
        return ProjectPaths(child(self.root, project_id))

    def read_backup(self, root):
        data = read_domain_record(root / "project.json")
        child(root.parent, data["id"])
        return Project(**{**data, "paths": ProjectPaths(root), "components": tuple(data["components"]),
                          "config": ProjectConfig.from_dict(data["config"])})

    @workspace_locked
    def save(self, project: Project) -> None:
        project.config.validate()
        project.validate_conversation_storage()
        data = record(project)
        data["config"] = project.config.to_dict()
        atomic_domain_json(project.paths.root / "project.json", data)
        log_event(project.paths.logs, "project.saved", entity_id=project.id)

    @workspace_locked
    def load(self, project_id: str) -> Project:
        paths = self.paths(project_id)
        data = read_domain_record(paths.root / "project.json")
        if data["id"] != project_id:
            raise ValueError("Project ID mismatch")
        log_event(paths.logs, "project.loaded", entity_id=project_id)
        if "components" not in data or "conversation_storage" not in data:
            raise ValueError("Project metadata requires components and conversation_storage")
        selected = data["components"]
        if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
            raise ValueError("Invalid Project component selection")
        return Project(**{**data, "components": tuple(selected),
                          "config": ProjectConfig.from_dict(data["config"]), "paths": paths})

    @workspace_locked
    def delete(self, project: Project) -> None:
        """Remove an owned Project tree; lifecycle checks belong to the manager."""
        if project.paths.root.absolute() != self.paths(project.id).root.absolute():
            raise ValueError("Project path mismatch")
        self.load(project.id)
        remove_owned_tree(self.root, project.paths.root, project.id)
        log_event(self.root / "logs", "project.deleted", entity_id=project.id,
                  permanent=True)

    @workspace_locked
    @queryable
    def list(self, *, include_deleted: bool = False) -> list[Project]:
        projects = [self.load(path.parent.name) for path in self.root.glob("*/project.json")]
        return sorted((project for project in projects if include_deleted or not project.deleted),
                      key=lambda project: (project.created_at, project.id))


# 프로젝트 수명 주기와 하위 도메인 초기화를 조율한다.
class ProjectManager:
    def __init__(self, repository: ProjectRepository, tasks: Optional[TaskManager] = None, *,
                 components: Optional[Union[ComponentRegistry, Iterable[ProjectComponent]]] = None,
                 backups=None, backup_steps=None, configuration_validator=None) -> None:
        registry = (components if isinstance(components, ComponentRegistry)
                    else ComponentRegistry(tuple(components) if components is not None else ()))
        self.repository = repository
        self.tasks = tasks if tasks is not None else TaskManager()
        self.bind_configuration_validator(configuration_validator)
        if self.tasks.run_repository is None:
            from llm.services.runtime.runs import RunRepository
            self.tasks.run_repository = RunRepository()
        self.access = ProjectAccess(repository)
        self.results = RunResultQuery(self.tasks, self.tasks.run_repository)
        self.ownership = repository.ownership
        self.tasks.bind_project_access(self.access)
        self.components = registry
        from llm.services.runtime.policies import model_token_count
        self.usage_counters = {"model_default": model_token_count}
        self.backups = backups if backups is not None else DirectoryBackups()
        from llm.services.lifecycle.steps import StepRepository
        self.backup_steps = backup_steps if backup_steps is not None else StepRepository()

    def _default_path(self) -> Path:
        path = self.repository.root / "default-project.json"
        for entry in (path, *path.parents):
            if entry.is_symlink():
                raise ValueError("Default Project reference cannot follow linked paths")
        return path

    def _validate_configuration(self, project) -> None:
        self.components.validate_configuration(project)
        if self.configuration_validator is not None:
            self.configuration_validator(project.config)
            if project.config.task_defaults:
                self.configuration_validator(project.config, task_config=project.config.task_defaults)

    def _create(self, project_id: str, title: str, *, config: Optional[ProjectConfig] = None,
                components: tuple[str, ...] = (),
                conversation_storage: Optional[str] = None) -> Project:
        selected = self.components.validate(components)
        storage = self.tasks.conversations.resolve(conversation_storage)
        project = Project(project_id, title, self.repository.paths(project_id),
                          config=deepcopy(config) if config else ProjectConfig(), components=selected,
                          conversation_storage=storage)
        self._validate_configuration(project)
        self.repository.save(project)
        try:
            self.tasks.initialize(deepcopy(project))
            self.components.initialize(project)
        except Exception:
            project.deleted = True
            self.repository.save(project)
            log_event(project.paths.logs, "project.initialization_failed", entity_id=project.id)
            raise
        log_event(project.paths.logs, "project.created", entity_id=project.id)
        return project

    def _validate_archive(self, root):
        project = self.repository.read_backup(root)
        if project.conversation_storage != "file":
            raise ValueError("Portable backup requires file conversation storage")
        self.components.validate(project.components)
        self._validate_configuration(project)
        for name in project.components:
            validator = getattr(self.components.get(name), "validate_backup", None)
            if validator is None:
                raise ValueError("Component must implement validate_backup for portable backup")
            validator(project)
        for task in self.tasks.repository.read_backup(project):
            if self.configuration_validator is not None:
                self.configuration_validator(project.config, task_config=task.config)
            for run in self.tasks.run_repository.read_backup(task):
                self.backup_steps.read_backup(run)
        return project

    def _backup_destination(self, destination):
        target = Path(destination).absolute()
        root = self.repository.root.absolute()
        if target == root or root in target.parents:
            raise ValueError("Backup destination must be outside the projects directory")
        return target

    # 공개 API
    def bind_configuration_validator(self, validator) -> None:
        """Project/Task 설정 검증을 같은 계약에 연결한다. 저장소와 Engine 실행은 분리한다."""
        if validator is not None and not callable(validator):
            raise TypeError("Configuration validator must be callable")
        self.configuration_validator = validator
        self.tasks.bind_configuration_validator(validator)

    @workspace_locked
    def model_usage(self, project):
        """Run과 컴포넌트의 원본 영수증을 조회한다. 프로젝트 합계 파일은 만들지 않는다."""
        from llm.services.runtime.usage import component_usage
        from datetime import datetime, timezone, timedelta
        current = self.access.require(project)
        records = [{**entry, "source": "run", "task_id": task.id, "run_id": run.id}
                   for task in self.tasks.list(current, include_deleted=True)
                   for run in self.tasks.run_repository.list(task)
                   for entry in run.metadata.get("completions", [])]
        records.extend({**entry, "source": "component"} for entry in component_usage(self.components, current))
        period = current.config.policies["usage"]["period_seconds"]
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=period) if period is not None else None
        records = [r for r in records if cutoff is None or datetime.fromisoformat(r["started_at"]) >= cutoff]
        known, reserved, unknown = 0, 0, 0
        for entry in records:
            tokens = entry.get("usage", {}).get("total_tokens")
            if entry.get("usage_complete") and type(tokens) is int:
                known += tokens
            elif entry.get("reserved_tokens") is not None:
                reserved += entry["reserved_tokens"]
            else:
                unknown += 1
        return {"calls": records, "call_count": len(records), "known_tokens": known,
                "reserved_tokens": reserved, "unknown_calls": unknown, "period_seconds": period}

    @workspace_locked
    def maintenance(self, project, *, apply=False, expected_version=None):
        """컴포넌트가 소유한 자료의 정리 계획. 각 컴포넌트가 자신의 경로와 활성 작업을 보호한다."""
        current = self.access.require(project)
        plans = {}
        for name in current.components:
            hook = getattr(self.components.get(name), "maintenance", None)
            if hook:
                plans[name] = hook(current)
        version = revision_token({"project": record(current), "plans": plans})
        if apply:
            if not expected_version or expected_version != version:
                raise ValueError("maintenance_conflict: review a fresh maintenance plan")
            for name, plan in plans.items():
                if plan["candidates"] or plan.get("pending"):
                    self.components.get(name).maintenance(current, apply=True, expected_version=plan["version"])
        return {"components": plans, "version": version}

    @workspace_locked
    def recovery(self, project, *, apply=False, expected_version=None):
        from llm.services.history.recovery import ProjectRecovery
        current = self.access.require(project)
        service = ProjectRecovery(self)
        return service.apply(current, expected_version) if apply else service.plan(current)

    @workspace_locked
    def recover_deletions(self):
        """실행 런타임이 없는 workspace에서 이전에 시작한 Project/Task 삭제만 마무리한다."""
        if self.ownership.attached_tasks:
            raise ValueError("Shut down Task runtimes before deletion recovery")
        result = {"projects": recover_deletions(self.repository.root), "tasks": {}}
        for project in self.repository.list(include_deleted=True):
            result["tasks"][project.id] = recover_deletions(project.paths.tasks)
        return result

    @workspace_locked
    def recover_retention(self, project):
        from llm.services.history.retention import HistoryRetention
        return HistoryRetention(self).recover(self.access.require(project))

    @workspace_locked
    def retention(self, project, *, counter=None, expected_version=None, apply=False):
        """종료된 Task 단위로 보관 정책을 평가한다. 적용하려면 미리보기 version을 제출한다."""
        from llm.services.history.retention import HistoryRetention
        current = self.access.require(project)
        service = HistoryRetention(self, counter)
        return service.apply(current, expected_version) if apply else service.plan(current)

    @workspace_locked
    def create(self, title: str, *, config: Optional[ProjectConfig] = None,
               components: tuple[str, ...] = (),
               conversation_storage: Optional[str] = None) -> Project:
        return self._create(new_id(), title, config=config, components=components,
                            conversation_storage=conversation_storage)

    @workspace_locked
    def get_default(self, *, title: str = "Default project",
                    config: Optional[ProjectConfig] = None) -> Project:
        """최초에는 전체 등록 Component·file 대화 저장을 선택하고, 이후에는 기존 설정을 보존한다."""
        path = self._default_path()
        pointer = read_json(path) if path.exists() else None
        if pointer is not None:
            if (not isinstance(pointer, dict) or type(pointer.get("pending")) is not bool
                    or not isinstance(pointer.get("project_id"), str)):
                raise ValueError("Invalid default Project reference")
            metadata = self.repository.paths(pointer["project_id"]).root / "project.json"
            if metadata.exists():
                current = self.access.load(pointer["project_id"])
                self.components.validate(current.components)
                if pointer["pending"]:
                    # 생성 도중 종료된 경우 같은 Project의 멱등 초기화만 마무리한다.
                    self.tasks.initialize(deepcopy(current))
                    self.components.initialize(current)
                    atomic_json(path, {"project_id": current.id, "pending": False})
                return current
        # 소프트 삭제는 위 access.load에서 거부한다. 영구 삭제 후에는 새 ID로 만든다.
        settings = deepcopy(config) if config is not None else ProjectConfig()
        settings["default_engine"] = "loop"
        settings.validate()
        self.tasks.conversations.resolve("file")
        selected = self.components.validate(self.components.names())
        identifier = pointer["project_id"] if pointer is not None and pointer["pending"] else new_id()
        self._validate_configuration(Project(identifier, title, self.repository.paths(identifier),
                                     config=settings, components=selected, conversation_storage="file"))
        # ID를 먼저 예약하므로 포인터 공개 실패/재시작에도 같은 Project를 재사용한다.
        atomic_json(path, {"project_id": identifier, "pending": True})
        project = self._create(identifier, title, config=settings, components=selected,
                               conversation_storage="file")
        atomic_json(path, {"project_id": project.id, "pending": False})
        return project

    @workspace_locked
    def configuration(self, project: Project, *, config=None, expected_version=None) -> dict:
        """UI용 설정 스냅샷. Component 설정을 중복 저장하지 않고 담당 객체에 조회를 위임한다."""
        current = self.access.require(project)
        check_revision(current.config.to_dict(), expected_version)
        version = revision_token(current.config.to_dict())
        configurations = {name: self.components.get(name).configuration(deepcopy(current))
                          for name in current.components}
        versions = {name: revision_token(value) for name, value in configurations.items()}
        if config is not None:
            current.config = ProjectConfig(config)
            self._validate_configuration(current)
            configurations = {}
        self.components.validate(current.components)
        for name in dict.fromkeys((*current.components, *current.config.component_configurations)):
            if name not in configurations:
                configurations[name] = self.components.get(name).configuration(deepcopy(current))
        return {"project": {"id": current.id, "title": current.title,
                            "conversation_storage": current.conversation_storage,
                            "config": current.config.to_dict()},
                "policy_schema": ProjectConfig.policy_schema(),
                "config_version": version,
                "component_versions": versions,
                "component_configurations": configurations,
                "components": {name: {"directory": self.components.get(name).directory,
                    "configuration": deepcopy(configurations[name]),
                    "effective": component_configuration(self.components.get(name), deepcopy(current))}
                    for name in current.components}}

    @workspace_locked
    def configure_policies(self, project: Project, changes: dict, *, expected_version=None) -> dict:
        """최신 설정에 정책 변경만 병합한다. 실행 중 Run의 사본은 바꾸지 않는다."""
        current = self.access.require(project)
        check_revision(current.config.to_dict(), expected_version)
        policies = current.config.configure_policies(changes)
        self._validate_configuration(current)
        self.repository.save(current)
        return policies

    @workspace_locked
    def save(self, project: Project, *, expected_version=None) -> None:
        current = self.access.require(project)
        check_revision(current.config.to_dict(), expected_version)
        if project.deleted != current.deleted or project.components != current.components:
            raise ValueError("Use lifecycle or component APIs to change managed Project state")
        if project.conversation_storage != current.conversation_storage:
            storage = self.tasks.conversations.resolve(project.conversation_storage)
            if self.tasks.list(current, include_deleted=True):
                raise ValueError("Cannot change conversation storage after Tasks have been created")
            current.conversation_storage = storage
        current.title, current.config = project.title, deepcopy(project.config)
        self._validate_configuration(current)
        self.repository.save(current)

    @workspace_locked
    def set_components(self, project: Project, names: tuple[str, ...]) -> None:
        current = self.access.require(project)
        current.components = self.components.validate(names)
        # Initialization is idempotent. Failed initialization does not publish
        # the changed selection; existing component data is never removed.
        self.components.initialize(current)
        self.repository.save(current)
        project.components = current.components

    @workspace_locked
    def configure_component(self, project: Project, name: str, configuration: dict) -> None:
        current = self.access.require(project)
        self.component(current, name).configure(configuration)
        project.config = self.access.require(current).config

    @workspace_locked
    def component(self, project: Project, name: str) -> ComponentData:
        current = self.access.require(project)
        self.components.validate(current.components)
        if name not in current.components:
            raise ValueError("Component is not enabled for this Project")
        component = self.components.get(name)
        data_class = getattr(component, "data_class", None) or ComponentData
        return data_class(self.access, self.components, current, name).bind_model_usage(self.tasks, self.usage_counters)

    @workspace_locked
    def remove_component(self, project: Project, name: str, *, permanent: bool = False) -> None:
        """Disable by default; permanent removal also deletes its owned directory."""
        if type(permanent) is not bool:
            raise TypeError("permanent must be a bool")
        current = self.access.require(project)
        component = self.components.get(name)
        if permanent:
            from datetime import datetime, timezone, timedelta
            usage = current.config.policies["usage"]
            reader = getattr(component, "model_usage", None)
            receipts = reader(current) if reader and component.root(current).exists() else []
            period = usage["period_seconds"]
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=period) if period is not None else None
            if any(r.get("status") == "started" for r in receipts):
                raise ValueError("Component has unfinished model usage reservations")
            if (usage["project_max_calls"] is not None or usage["project_max_tokens"] is not None) and any(
                    cutoff is None or datetime.fromisoformat(r["started_at"]) >= cutoff for r in receipts):
                raise ValueError("Component usage receipts are required by the active quota period")
            for task in self.tasks.list(current, include_deleted=True):
                self.tasks.require_inactive(task)
        current.components = tuple(item for item in current.components if item != name)
        if permanent:
            current.config.component_configurations.pop(name, None)
        self.components.validate(current.components)
        # Publish disabled state first: interrupted removal must not leave a
        # selected component exposing partially deleted definitions to new Runs.
        self.repository.save(current)
        project.components = current.components
        if permanent:
            component.delete_directory(current)
        log_event(current.paths.logs, "component.removed", entity_id=current.id,
                  permanent=permanent)

    @workspace_locked
    def load(self, project_id: str) -> Project:
        return self.repository.load(project_id)

    @workspace_locked
    @queryable
    def list(self, *, include_deleted: bool = False) -> list[Project]:
        return self.repository.list(include_deleted=include_deleted)

    @workspace_locked
    def delete(self, project: Project, *, permanent: bool = False) -> None:
        """Mark deleted by default; permanent=True removes the entire owned tree."""
        if type(permanent) is not bool:
            raise TypeError("permanent must be a bool")
        current = self.access.require(project, allow_deleted=True)
        tasks = self.tasks.list(current, include_deleted=True)
        for task in tasks:
            self.tasks.require_inactive(task)
        if permanent:
            self.repository.delete(current)
            for task in tasks:
                self.tasks._discard_conversation(task)
        else:
            current.deleted = True
            self.repository.save(current)
            log_event(current.paths.logs, "project.deleted", entity_id=current.id,
                      permanent=False)
        project.deleted = True

    @workspace_locked
    def restore(self, project: Project) -> None:
        current = self.access.require(project, allow_deleted=True)
        # Re-run selected, idempotent components before activating a Project
        # whose initialization may previously have failed.
        self.components.initialize(current)
        current.deleted = False
        self.repository.save(current)
        try:
            self.tasks.initialize(deepcopy(current))
        except Exception:
            current.deleted = True
            self.repository.save(current)
            raise
        project.deleted = False
        log_event(current.paths.logs, "project.restored", entity_id=current.id)

    @workspace_locked
    def clone(self, source: Project, *, title: Optional[str] = None) -> Project:
        source = self.access.require(source)
        self.components.validate(source.components)
        tasks = self.tasks.list(source)
        for task in tasks:
            self.tasks.require_inactive(task)
        clone = self.create(title if title is not None else source.title, config=source.config,
                            components=source.components,
                            conversation_storage=source.conversation_storage)
        try:
            self.components.clone(source, clone)
            for task in tasks:
                self.tasks.clone(task, clone)
        except Exception:
            clone.deleted = True
            self.repository.save(clone)
            raise
        log_event(clone.paths.logs, "project.cloned", entity_id=clone.id,
                  related_id=source.id)
        return clone

    @workspace_locked
    def backup(self, project: Project, destination: Path) -> Path:
        """Task 런타임 해제 후 전체 프로젝트를 복사한다. 메모리/외부 대화는 지원하지 않는다."""
        current = self.access.require(project, allow_deleted=True)
        for task in self.tasks.list(current, include_deleted=True):
            self.tasks.require_inactive(task)
        target = self._backup_destination(destination)
        with ExitStack() as stack:
            for name in current.components:
                component = self.components.get(name)
                scope = getattr(component, "backup_scope", None)
                if scope is None:
                    raise ValueError("Component must implement backup_scope for portable backup")
                stack.enter_context(scope(current))
            self._validate_archive(current.paths.root)
            return self.backups.create(current.paths.root, target, metadata={"project_id": current.id})

    @workspace_locked
    def restore_backup(self, source: Path) -> Project:
        """동일 ID가 없는 workspace에 복원한다. 기존 프로젝트/실행은 덮어쓰거나 재실행하지 않는다."""
        self.backups.verify(source)
        project = self._validate_archive(Path(source) / "project")
        destination = self.repository.paths(project.id).root
        self.backups.restore(source, destination,
                             validate=self._validate_archive)
        return self.repository.load(project.id)

    @workspace_locked
    def upgrade_backup(self, source: Path, destination: Path, *, transform) -> Path:
        """개발자가 제공한 변환 함수를 별도 백업에 적용하고 현재 형식으로 검증한다."""
        return self.backups.upgrade(source, self._backup_destination(destination), transform=transform,
                                   validate=self._validate_archive)
