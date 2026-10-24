"""Project의 생성, 저장, 복제, 삭제와 Component 초기화를 조율한다. 실제 하위 디렉토리 구조는 Session/Component에 위임한다."""

from llm.services.infrastructure.storage import (
    read_domain_record,
    atomic_domain_json,
    revision_token,
    check_revision,
    recover_deletions,
)

from llm.services.query import queryable
from typing import Iterable, Optional, Union
from copy import deepcopy
from contextlib import ExitStack
from llm.services.infrastructure.backups import DirectoryBackups
from pathlib import Path

from llm.core.models import ConversationStorage, Project, ProjectConfig, new_id
from llm.core.results import CompletionRecords
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.storage import child, record, remove_owned_tree
from llm.services.lifecycle.sessions import SessionManager
from llm.services.infrastructure.logging import log_event
from llm.services.lifecycle.access import ProjectAccess
from llm.services.lifecycle.agents import AgentRepository, AgentManager
from llm.services.results import RunResultQuery
from llm.services.schema import ProjectConfiguration
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import WorkspaceOwnership, workspace_locked
from llm.components.registry import ComponentRegistry
from llm.components.base import ProjectComponent, Component, ComponentHook
from llm.services.configuration_records import ConfigurationValidator
from .lifecycle_records import (
    ProjectRecordIdentity,
    ProjectBackupMetadata,
    DeletionRecoveryResults,
    LifecycleDeletion,
    ProjectLogEvent,
    ComponentLogEvent,
)


# 프로젝트 JSON 저장과 경로/소유권 검사를 담당한다.
class ProjectRepository:

    CURRENT_RECORDS = False
    ALL_RECORDS = True
    STORAGE_EXISTS = "Project storage already exists"
    PATH_MISMATCH = "Project path mismatch"
    ID_MISMATCH = "Project ID mismatch"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.ownership = WorkspaceOwnership(root)
        self.agents = AgentRepository(self.ownership)

    def paths(self, project_id: str) -> ProjectPaths:
        return ProjectPaths(child(self.root, project_id))

    def read_backup(self, root):
        data = read_domain_record(ProjectPaths(root).record)
        child(root.parent, ProjectRecordIdentity(data).id)
        project = Project.from_record(data, ProjectPaths(root))
        self.agents.read_backup(project)
        return project

    @workspace_locked
    def create(self, project: Project) -> None:
        if project.paths.root.exists():
            raise FileExistsError(ProjectRepository.STORAGE_EXISTS)
        self.agents.initialize_new(project)
        self._write(project)

    @workspace_locked
    def save(self, project: Project) -> None:
        self.load(project.id)
        self._write(project)

    def _write(self, project: Project) -> None:
        if project.paths.root.absolute() != self.paths(project.id).root.absolute():
            raise ValueError(ProjectRepository.PATH_MISMATCH)
        project.config.validate()
        project.validate_conversation_storage()
        data = project.to_record()
        atomic_domain_json(project.paths.record, data)
        log_event(project.paths.logs, ProjectLogEvent.SAVED, entity_id=project.id)

    @workspace_locked
    def load(self, project_id: str) -> Project:
        paths = self.paths(project_id)
        data = read_domain_record(paths.record)
        if ProjectRecordIdentity(data).id != project_id:
            raise ValueError(ProjectRepository.ID_MISMATCH)
        log_event(paths.logs, ProjectLogEvent.LOADED, entity_id=project_id)
        project = Project.from_record(data, paths)
        self.agents.validate_backup(project)
        return project

    @workspace_locked
    def delete(self, project: Project) -> None:
        """Remove an owned Project tree; lifecycle checks belong to the manager."""
        if project.paths.root.absolute() != self.paths(project.id).root.absolute():
            raise ValueError(ProjectRepository.PATH_MISMATCH)
        self.load(project.id)
        remove_owned_tree(self.root, project.paths.root, project.id)
        log_event(
            self.root / ProjectPaths.LOGS,
            ProjectLogEvent.DELETED,
            entity_id=project.id,
            permanent=LifecycleDeletion.PERMANENT,
        )

    @workspace_locked
    @queryable
    def list(self, *, include_deleted: bool = CURRENT_RECORDS) -> list[Project]:
        projects = [self.load(path.parent.name) for path in self.root.glob(ProjectPaths.CHILD_RECORD_PATTERN)]
        return sorted(
            (project for project in projects if include_deleted or not project.deleted),
            key=lambda project: (project.created_at, project.id),
        )


# 프로젝트 수명 주기와 하위 도메인 초기화를 조율한다.
class ProjectManager:

    UNSPECIFIED = None
    ARCHIVE_VERIFICATION = True
    NEWEST_FIRST = True
    APPLY_RECOVERY = False
    UNPORTABLE_STORAGE = "Portable backup requires file conversation storage"
    MISSING_BACKUP_AGENT = "Backup Session references a missing Project Agent"
    MISSING_BACKUP_VALIDATOR = "Component must implement validate_backup to verify stored evidence"
    INTERNAL_BACKUP = "Backup destination must be outside the projects directory"
    ACTIVE_DELETION_RECOVERY = "Shut down Session runtimes before deletion recovery"
    STALE_COMPONENTS = "Project components changed; reload configuration"
    MANAGED_STATE = "Use lifecycle or component APIs to change managed Project state"
    CONVERSATION_STORAGE_IN_USE = "Cannot change conversation storage after Sessions have been created"
    PENDING_MODEL_USAGE = "Component has unfinished model usage reservations"
    REQUIRED_USAGE_RECEIPTS = "Component usage receipts are required by the active quota period"
    MISSING_BACKUP_SCOPE = "Component must implement backup_scope for portable backup"

    def __init__(
        self,
        repository: ProjectRepository,
        sessions: Optional[SessionManager] = UNSPECIFIED,
        *,
        components: Optional[Union[ComponentRegistry, Iterable[ProjectComponent]]] = UNSPECIFIED,
        backups=UNSPECIFIED,
        backup_steps=UNSPECIFIED,
        config_validator=UNSPECIFIED,
        engines=UNSPECIFIED,
    ) -> None:
        registry = (
            components
            if isinstance(components, ComponentRegistry)
            else ComponentRegistry(tuple(components) if components is not ProjectManager.UNSPECIFIED else ())
        )
        self.repository = repository
        self.sessions = sessions if sessions is not ProjectManager.UNSPECIFIED else SessionManager()
        self.bind_config_validator(config_validator)
        if self.sessions.run_repository is ProjectManager.UNSPECIFIED:
            from llm.services.runtime.runs import RunRepository

            self.sessions.run_repository = RunRepository()
        self.access = ProjectAccess(repository)
        self.results = RunResultQuery(self.sessions, self.sessions.run_repository)
        self.ownership = repository.ownership
        self.sessions.bind_project_access(self.access)
        self.components = registry
        self.sessions.components = registry
        from llm.services.runtime.policies import TokenCounters

        self.usage_counters = TokenCounters.build()
        self.backups = backups if backups is not ProjectManager.UNSPECIFIED else DirectoryBackups()
        from llm.services.lifecycle.steps import StepRepository

        self.backup_steps = (
            backup_steps if backup_steps is not ProjectManager.UNSPECIFIED else StepRepository()
        )
        self.sessions.step_repository = self.backup_steps
        from llm.engines.registry import EngineRegistry

        self.agents = AgentManager(
            self.access,
            repository.agents,
            engines=EngineRegistry() if engines is ProjectManager.UNSPECIFIED else engines,
            components=registry,
            sessions=self.sessions,
            steps=self.backup_steps,
        )
        from llm.services.lifecycle.references import ProjectReferences

        self.access.agent_references = self.agents.references
        self.references = ProjectReferences(
            registry, repository.agents, selected_references=self.access.agent_references
        )
        self.sessions.agents = self.agents

    def _validate_config(self, project) -> None:
        self.components.validate_config(project)
        if self.config_validator is not ProjectManager.UNSPECIFIED:
            self.config_validator(project.config)

    def _create(
        self,
        project_id: str,
        title: str,
        *,
        config: Optional[ProjectConfig] = UNSPECIFIED,
        components: tuple[str, ...] = (),
        conversation_storage: Optional[str] = UNSPECIFIED,
    ) -> Project:
        selected = self.components.validate(components)
        storage = self.sessions.conversations.resolve(conversation_storage)
        project = Project(
            project_id,
            title,
            self.repository.paths(project_id),
            config=(
                ProjectConfig() if config is ProjectManager.UNSPECIFIED else ProjectConfig.from_dict(config)
            ),
            components=selected,
            conversation_storage=storage,
        )
        self._validate_config(project)
        project.components = ()
        self.repository.create(project)
        project.components = selected
        self.components.initialize(project)
        self.repository.save(project)
        self.sessions.initialize(deepcopy(project))
        log_event(project.paths.logs, ProjectLogEvent.CREATED, entity_id=project.id)
        return project

    def _validate_archive(self, root):
        project = self.repository.read_backup(root)
        if project.conversation_storage != ConversationStorage.FILE:
            raise ValueError(ProjectManager.UNPORTABLE_STORAGE)
        self.components.validate(project.components)
        self._validate_config(project)
        self._validate_components(project)
        agents = self.repository.agents.read_backup(project)
        for session in self.sessions.repository.read_backup(project):
            if not set(session.agent_ids) <= set(agents):
                raise ValueError(ProjectManager.MISSING_BACKUP_AGENT)
            if self.config_validator is not ProjectManager.UNSPECIFIED:
                self.config_validator(project.config, session_config=session.config)
            for run in self.sessions.run_repository.read_backup(session):
                self.backup_steps.read_backup(run)
        self.sessions.transfers.validate_project(project, archive=ProjectManager.ARCHIVE_VERIFICATION)
        return project

    def _validate_components(self, project):
        """Backup and parent deletion share each stored owner's integrity proof."""
        for name in self.components.stored(project):
            validator = getattr(
                self.components.get(name), ComponentHook.VALIDATE_BACKUP, ProjectManager.UNSPECIFIED
            )
            if validator is ProjectManager.UNSPECIFIED:
                raise ValueError(ProjectManager.MISSING_BACKUP_VALIDATOR)
            validator(project)

    def _backup_destination(self, destination):
        target = Path(destination).absolute()
        root = self.repository.root.absolute()
        if target == root or root in target.parents:
            raise ValueError(ProjectManager.INTERNAL_BACKUP)
        return target

    # 공개 API
    def bind_config_validator(self, validator) -> None:
        """Project/Session 설정 검증을 같은 계약에 연결한다. 저장소와 Engine 실행은 분리한다."""
        ConfigurationValidator.require(validator)
        self.config_validator = validator
        self.sessions.bind_config_validator(validator)

    @workspace_locked
    def activity(self, project, *, limit=UNSPECIFIED, newest_first=NEWEST_FIRST):
        """도메인 원본을 로드하지 않고 Project의 확정된 lifecycle 참조만 조회한다."""
        from llm.services.infrastructure.activity import read_activity

        current = self.access.require(project)
        return read_activity(current.paths, limit=limit, newest_first=newest_first)

    @workspace_locked
    def model_usage(self, project):
        """Run과 컴포넌트의 원본 영수증을 조회한다. 프로젝트 합계 파일은 만들지 않는다."""
        from llm.services.runtime.usage import component_usage
        from llm.core.usage import UsageCall, UsageSummary
        from llm.core.policies import UsagePolicy

        current = self.access.require(project)
        records = [
            UsageCall.for_run(result, run)
            for session in self.sessions.list(current, include_deleted=ProjectRepository.ALL_RECORDS)
            for run in self.sessions.run_repository.list(session)
            for result in CompletionRecords.results(run.metadata)
        ]
        records.extend(UsageCall.for_component(entry) for entry in component_usage(self.components, current))
        return UsageSummary.collect(records, UsagePolicy.from_policies(current.config.policies)).to_dict()

    @workspace_locked
    def recovery(self, project, *, apply=APPLY_RECOVERY, expected_version=UNSPECIFIED):
        from llm.services.history.recovery import ProjectRecovery

        current = self.access.require(project)
        service = ProjectRecovery(self)
        return service.apply(current, expected_version) if apply else service.plan(current)

    @workspace_locked
    def recover_deletions(self):
        """실행 런타임이 없는 workspace에서 이전에 시작한 Project/Session 삭제만 마무리한다."""
        if self.ownership.attached_sessions:
            raise ValueError(ProjectManager.ACTIVE_DELETION_RECOVERY)
        result = DeletionRecoveryResults(recover_deletions(self.repository.root), {})
        for project in self.repository.list(include_deleted=ProjectRepository.ALL_RECORDS):
            result.include_session(project.id, recover_deletions(project.paths.sessions))
        return result.to_dict()

    @workspace_locked
    def recover_retention(self, project):
        from llm.services.history.retention import HistoryRetention

        return HistoryRetention(self).recover(self.access.require(project))

    @workspace_locked
    def retention_plan(self, project, unit, *, counter=UNSPECIFIED, counter_params=UNSPECIFIED):
        """Inspect eligible history; retention selection belongs to Application."""
        from llm.services.history.retention import HistoryRetention

        current = self.access.require(project)
        return HistoryRetention(self, counter, counter_params=counter_params).plan(current, unit=unit)

    @workspace_locked
    def prune_history(self, project, unit, targets, *, expected_version):
        from llm.services.history.retention import HistoryRetention

        current = self.access.require(project)
        return HistoryRetention(self).apply(
            current, unit=unit, targets=targets, expected_version=expected_version
        )

    @workspace_locked
    def create(
        self,
        title: str,
        *,
        config: Optional[ProjectConfig] = UNSPECIFIED,
        components: tuple[str, ...] = (),
        conversation_storage: Optional[str] = UNSPECIFIED,
    ) -> Project:
        return self._create(
            new_id(), title, config=config, components=components, conversation_storage=conversation_storage
        )

    @workspace_locked
    def describe_config(
        self,
        project: Project,
        *,
        config=UNSPECIFIED,
        expected_version=UNSPECIFIED,
    ) -> dict:
        """UI용 설정 스냅샷. Component 설정을 중복 저장하지 않고 담당 객체에 조회를 위임한다."""
        current = self.access.require(project)
        check_revision(current.config.to_dict(), expected_version)
        version = revision_token(current.config.to_dict())
        configurations = {
            name: self.components.get(name).get_config(deepcopy(current)) for name in current.components
        }
        versions = {name: revision_token(value) for name, value in configurations.items()}
        if config is not ProjectManager.UNSPECIFIED:
            current.config = ProjectConfig.from_dict(config)
            self._validate_config(current)
            configurations = {}
        self.components.validate(current.components)
        for name in dict.fromkeys((*current.components, *current.config.parameter_targets.component_names)):
            if name not in configurations:
                configurations[name] = self.components.get(name).get_config(deepcopy(current))
        return ProjectConfiguration.snapshot(
            current,
            registry=self.components,
            configurations=configurations,
            version=version,
            component_versions=versions,
        )

    @workspace_locked
    def configure_policies(self, project: Project, changes: dict, *, expected_version=UNSPECIFIED) -> dict:
        """최신 설정에 정책 변경만 병합한다. 실행 중 Run의 사본은 바꾸지 않는다."""
        current = self.access.require(project)
        check_revision(current.config.to_dict(), expected_version)
        policies = current.config.configure_policies(changes)
        self._validate_config(current)
        self.repository.save(current)
        return policies

    @workspace_locked
    def save(
        self,
        project: Project,
        *,
        expected_version=UNSPECIFIED,
        components=UNSPECIFIED,
        expected_components=UNSPECIFIED,
    ) -> None:
        current = self.access.require(project)
        check_revision(current.config.to_dict(), expected_version)
        if (
            expected_components is not ProjectManager.UNSPECIFIED
            and tuple(expected_components) != current.components
        ):
            raise ValueError(ProjectManager.STALE_COMPONENTS)
        if (
            project.deleted != current.deleted
            or project.components != current.components
            or project.initialized_components != current.initialized_components
        ):
            raise ValueError(ProjectManager.MANAGED_STATE)
        previous_components = current.components
        if components is not ProjectManager.UNSPECIFIED:
            self.references.validate_selection(current, components)
            current.components = self.components.validate(components)
        if project.conversation_storage != current.conversation_storage:
            storage = self.sessions.conversations.resolve(project.conversation_storage)
            if self.sessions.list(current, include_deleted=ProjectRepository.ALL_RECORDS):
                raise ValueError(ProjectManager.CONVERSATION_STORAGE_IN_USE)
            current.conversation_storage = storage
        current.title, current.config = project.title, deepcopy(project.config)
        self._validate_config(current)
        if current.components != previous_components:
            # Prepare component-owned storage before publishing one metadata update.
            # Initialization is idempotent; a failure must not publish draft configuration.
            self.components.initialize(current)
        self.repository.save(current)

    @workspace_locked
    def set_components(self, project: Project, names: tuple[str, ...]) -> None:
        current = self.access.require(project)
        self.references.validate_selection(current, names)
        current.components = self.components.validate(names)
        # Initialization is idempotent. Failed initialization does not publish
        # the changed selection; existing component data is never removed.
        self.components.initialize(current)
        self.repository.save(current)
        project.components = current.components
        project.initialized_components = current.initialized_components

    @workspace_locked
    def configure_component(self, project: Project, name: str, configuration: dict) -> None:
        current = self.access.require(project)
        self.component(current, name).configure(configuration)
        project.config = self.access.require(current).config

    @workspace_locked
    def component(self, project: Project, name: str) -> ComponentData:
        current = self.access.require(project)
        self.components.validate(current.components)
        ComponentRegistry.require_enabled(current, name)
        component = self.components.get(name)
        data_class = Component.data_type(component) or ComponentData
        return (
            data_class(self.access, self.components, current, name)
            .bind_resources(self.sessions, self.backup_steps, agents=self.agents)
            .bind_model_usage(self.sessions, self.usage_counters)
        )

    @workspace_locked
    def remove_component(
        self, project: Project, name: str, *, permanent: bool = LifecycleDeletion.DEFAULT
    ) -> None:
        """Disable by default; permanent removal also deletes its owned directory."""
        LifecycleDeletion.validate(permanent)
        current = self.access.require(project)
        component = self.components.get(name)
        # 파괴적 디렉토리 삭제보다 먼저 선택 불변식을 검증한다.
        self.references.validate_selection(
            current, tuple(item for item in current.components if item != name)
        )
        if permanent:
            self.references.require_unreferenced(current, name)
        self.components.validate(tuple(item for item in current.components if item != name))
        if permanent:
            from llm.services.runtime.usage import project_usage_policies, protects_project_usage
            from llm.components.usage import ModelUsageStatus, ModelUsageRecord

            reader = getattr(component, ComponentHook.MODEL_USAGE, ProjectManager.UNSPECIFIED)
            receipts = reader(current) if reader else []
            if any(ModelUsageRecord.status_from_record(r) == ModelUsageStatus.STARTED for r in receipts):
                raise ValueError(ProjectManager.PENDING_MODEL_USAGE)
            if protects_project_usage(receipts, project_usage_policies(current, self.sessions)):
                raise ValueError(ProjectManager.REQUIRED_USAGE_RECEIPTS)
            for session in self.sessions.list(current, include_deleted=ProjectRepository.ALL_RECORDS):
                self.sessions.require_inactive(session)
            component.delete_directory(current)
        current.components = tuple(item for item in current.components if item != name)
        if permanent:
            current.config.remove_component_parameters(name)
            current.initialized_components = tuple(
                item for item in current.initialized_components if item != name
            )
        self.components.validate(current.components)
        # 선택 해제와 디렉토리 격리를 같은 트랜잭션으로 확정한다.
        self.repository.save(current)
        project.components = current.components
        project.initialized_components = current.initialized_components
        log_event(current.paths.logs, ComponentLogEvent.REMOVED, entity_id=current.id, permanent=permanent)

    @workspace_locked
    def load(self, project_id: str) -> Project:
        return self.repository.load(project_id)

    @workspace_locked
    @queryable
    def list(self, *, include_deleted: bool = ProjectRepository.CURRENT_RECORDS) -> list[Project]:
        return self.repository.list(include_deleted=include_deleted)

    @workspace_locked
    def delete(self, project: Project, *, permanent: bool = LifecycleDeletion.DEFAULT) -> None:
        """Mark deleted by default; permanent=True removes the entire owned tree."""
        LifecycleDeletion.validate(permanent)
        current = self.access.require(project, allow_deleted=ProjectAccess.INCLUDING_DELETED)
        sessions = self.sessions.list(current, include_deleted=ProjectRepository.ALL_RECORDS)
        for session in sessions:
            self.sessions.require_inactive(session)
        if permanent:
            self._validate_components(current)
            for session in sessions:
                self.sessions.validate_removal(session, project_ending=SessionManager.PROJECT_ENDING)
            self.repository.delete(current)
            for session in sessions:
                self.sessions._discard_conversation(session)
        else:
            current.deleted = Project.DELETED
            self.repository.save(current)
            log_event(
                current.paths.logs,
                ProjectLogEvent.DELETED,
                entity_id=current.id,
                permanent=LifecycleDeletion.DEFAULT,
            )
        project.deleted = Project.DELETED

    @workspace_locked
    def restore(self, project: Project) -> None:
        current = self.access.require(project, allow_deleted=ProjectAccess.INCLUDING_DELETED)
        # Re-run selected, idempotent components before activating a Project
        # whose initialization may previously have failed.
        self.components.initialize(current)
        current.deleted = Project.ACTIVE
        self.repository.save(current)
        self.sessions.initialize(deepcopy(current))
        project.deleted = Project.ACTIVE
        log_event(current.paths.logs, ProjectLogEvent.RESTORED, entity_id=current.id)

    @workspace_locked
    def clone(self, source: Project, *, title: Optional[str] = UNSPECIFIED) -> Project:
        source = self.access.require(source)
        self.components.validate(source.components)
        sessions = self.sessions.list(source)
        for session in sessions:
            self.sessions.require_inactive(session)
        clone = self.create(
            title if title is not ProjectManager.UNSPECIFIED else source.title,
            config=source.config,
            components=source.components,
            conversation_storage=source.conversation_storage,
        )
        self.components.clone(source, clone)
        self.repository.agents.clone(source, clone)
        for session in sessions:
            self.sessions.clone(session, clone)
        log_event(clone.paths.logs, ProjectLogEvent.CLONED, entity_id=clone.id, related_id=source.id)
        return clone

    @workspace_locked
    def backup(self, project: Project, destination: Path) -> Path:
        """Session 런타임 해제 후 전체 프로젝트를 복사한다. 메모리/외부 대화는 지원하지 않는다."""
        current = self.access.require(project, allow_deleted=ProjectAccess.INCLUDING_DELETED)
        for session in self.sessions.list(current, include_deleted=ProjectRepository.ALL_RECORDS):
            self.sessions.require_inactive(session)
        target = self._backup_destination(destination)
        with ExitStack() as stack:
            for name in self.components.stored(current):
                component = self.components.get(name)
                scope = getattr(component, ComponentHook.BACKUP_SCOPE, ProjectManager.UNSPECIFIED)
                if scope is ProjectManager.UNSPECIFIED:
                    raise ValueError(ProjectManager.MISSING_BACKUP_SCOPE)
                stack.enter_context(scope(current))
            self._validate_archive(current.paths.root)
            return self.backups.create(
                current.paths.root, target, metadata=ProjectBackupMetadata(current.id).to_dict()
            )

    @workspace_locked
    def restore_backup(self, source: Path) -> Project:
        """동일 ID가 없는 workspace에 복원한다. 기존 프로젝트/실행은 덮어쓰거나 재실행하지 않는다."""
        self.backups.verify(source)
        project = self._validate_archive(DirectoryBackups.project_root(source))
        destination = self.repository.paths(project.id).root
        self.backups.restore(source, destination, validate=self._validate_archive)
        return self.repository.load(project.id)
