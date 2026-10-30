"""Session의 영속 상태와 수명 주기를 관리한다. SessionRuntime은 실행 전용이며 RunManager가 소유한다. 제목/메타데이터 수정은 실행 설정 변경과 분리한다."""

from .lifecycle_records import (
    SessionRecordIdentity,
    ResumeTurnDetails,
    TurnResumeEvidence,
    TurnDeletionEvidence,
    LifecycleDeletion,
    SessionLogEvent,
)
from llm.services.configuration_records import ConfigurationValidator

from llm.core.history_references import HistoryReferenceSet

from llm.core.contracts import ActivityEventType

from llm.core.plans import PlanBlockerCode

from llm.core.models import ResourceKind

from llm.services.infrastructure.storage import read_domain_record, atomic_domain_json
from llm.services.infrastructure.storage import make_directory
from llm.services.infrastructure.transactions import after_commit, current_transaction

from llm.services.query import queryable
from typing import Optional, Union
import asyncio
import threading
from dataclasses import dataclass, field
from copy import deepcopy
from collections.abc import Callable

from llm.core.models import (
    ConversationStorage,
    Message,
    MessageStatus,
    Project,
    ProjectConfig,
    Run,
    Session,
    SessionStatus,
    new_id,
)
from llm.core.json_data import JsonObject
from llm.core.configuration import UNSET
from llm.core.paths import ProjectPaths, SessionPaths
from llm.core.contracts import Diagnostic, ResourceRef
from llm.core.plans import TurnDeletionPlan
from llm.core.checkpoints import ResumeAbandonment
from llm.services.history.conversation import Conversation, ProjectConversations, conversation_store
from llm.services.history.context import ConversationContextBuilder
from llm.services.history.maintenance_records import HistoryMaintenanceStorage
from llm.services.lifecycle.access import ProjectAccess
from llm.services.infrastructure.locking import workspace_locked, WorkspaceLock
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.activity import activity_event
from llm.services.infrastructure.storage import (
    atomic_json,
    child,
    read_json,
    record,
    remove_owned_tree,
    revision_token,
)


@dataclass(slots=True)
class RunRuntime:
    """One Run's preparation, cancellable consumer and mandatory finalizer."""

    UNSPECIFIED = None
    INTERRUPTED = True

    run: Run
    task: Optional[asyncio.Task[None]] = None
    execution: Optional[asyncio.Task[None]] = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    interrupt_requested: bool = False

    def interrupt(self):
        """Deliver cancellation once; cleanup owns any still-running work."""
        if not self.interrupt_requested:
            self.interrupt_requested = RunRuntime.INTERRUPTED
            if self.execution is not RunRuntime.UNSPECIFIED:
                self.execution.cancel()


@dataclass(slots=True)
class SessionRuntime:
    """Runtime-only state, owned and scheduled exclusively by RunManager."""

    project: Project
    session: Session
    queue: asyncio.Queue[Optional[str]] = field(default_factory=asyncio.Queue)
    worker: Optional[asyncio.Task[None]] = None
    executions: dict[str, RunRuntime] = field(default_factory=dict)
    waiting: list[str] = field(default_factory=list)
    groups: dict[str, list[Run]] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False


# 프로젝트 아래의 Session 메타데이터를 읽고 쓴다.
class SessionRepository:
    """Session metadata and paths beneath the owning Project's sessions root."""

    CURRENT_RECORDS = False
    ALL_RECORDS = True
    CREATE = True
    UPDATE = False
    OWNERSHIP_MISMATCH = "Session ownership mismatch"
    BACKUP_OWNERSHIP_MISMATCH = "Backup Session ownership mismatch"
    ACTIVE_BACKUP = "Backup contains an active Session"
    STORAGE_EXISTS = "Session storage already exists"
    PATH_MISMATCH = "Session path ownership mismatch"
    HIDDEN_PREFIX = "."

    def _load(self, paths: SessionPaths, session_id: str, project_id: str) -> Session:
        data = read_domain_record(paths.record)
        if (
            SessionRecordIdentity(data).id != session_id
            or SessionRecordIdentity(data).project_id != project_id
        ):
            raise ValueError(SessionRepository.OWNERSHIP_MISMATCH)
        ProjectConfig.validate_session(SessionRecordIdentity(data).config)
        session = Session.from_record(data, paths)
        from llm.services.runtime.operations import OperationRepository

        OperationRepository().has_records(session)
        from llm.services.runtime.transfers import TransferLedger

        TransferLedger.read(session)
        log_event(paths.logs, SessionLogEvent.LOADED, entity_id=session_id)
        return session

    def read_backup(self, project):
        """Session 파일/대화만 검증한다. 실행 복구나 로그 쓰기를 하지 않는다."""
        from llm.services.history.conversation import ConversationStore

        sessions = []
        for folder in project.paths.sessions.iterdir():
            if not folder.is_dir():
                continue
            data = read_domain_record(SessionPaths(folder).record)
            if (
                child(project.paths.sessions, SessionRecordIdentity(data).id) != folder
                or SessionRecordIdentity(data).project_id != project.id
            ):
                raise ValueError(SessionRepository.BACKUP_OWNERSHIP_MISMATCH)
            session = Session.from_record(data, SessionPaths(folder))
            ProjectConfig.validate_session(session.config)
            if session.status == SessionStatus.RUNNING or session.active_run_ids:
                raise ValueError(SessionRepository.ACTIVE_BACKUP)
            ConversationStore(session.paths.conversation).list()
            from llm.services.runtime.operations import OperationRepository

            OperationRepository().validate_backup(session)
            from llm.services.runtime.transfers import TransferLedger

            TransferLedger.read(session)
            sessions.append(session)
        return sessions

    def read_transfers(self, session):
        """Required current ledger, owned by the selected Session Repository."""
        from llm.services.runtime.transfers import TransferLedger

        return TransferLedger.read(session).to_dict()

    def write_transfers(self, session, value):
        """Replace under workspace transaction/CAS; never initialize on update."""
        from llm.services.runtime.transfers import TransferLedger

        current = self.read_transfers(session)
        TransferLedger.validate(session, current).require_update(value)
        TransferLedger.write(session, value)

    # 공개 API
    def paths(self, project: Project, session_id: str) -> SessionPaths:
        return SessionPaths(child(project.paths.sessions, session_id))

    def initialize(self, project: Project) -> None:
        make_directory(project.paths.sessions)
        log_event(project.paths.logs, SessionLogEvent.INITIALIZED, entity_id=project.id)

    def create(self, session: Session) -> None:
        if session.paths.root.exists():
            raise FileExistsError(SessionRepository.STORAGE_EXISTS)
        self._write(session, creating=SessionRepository.CREATE)

    def save(self, session: Session) -> None:
        self._load(session.paths, session.id, session.project_id)
        self._write(session, creating=SessionRepository.UPDATE)

    def _write(self, session: Session, *, creating: bool) -> None:
        ProjectConfig.validate_session(session.config)
        from llm.services.runtime.operations import OperationRepository

        operations = OperationRepository()
        data = record(session)
        Session.from_record(data, session.paths)
        atomic_domain_json(session.paths.record, data)
        if creating:
            operations.initialize(session)
            from llm.services.runtime.transfers import TransferLedger

            TransferLedger.initialize(session)
        log_event(session.paths.logs, SessionLogEvent.SAVED, entity_id=session.id, status=session.status)

    def load(self, project: Project, session_id: str) -> Session:
        return self._load(self.paths(project, session_id), session_id, project.id)

    def reload(self, session: Session) -> Session:
        return self._load(session.paths, session.id, session.project_id)

    def delete(self, session: Session) -> None:
        """Remove the Session subtree only after validating layout and ownership."""
        root = session.paths.root
        if (
            root.name != session.id
            or root.parent.name != ProjectPaths.SESSIONS
            or root.parent.parent.name != session.project_id
        ):
            raise ValueError(SessionRepository.PATH_MISMATCH)
        self.reload(session)
        remove_owned_tree(root.parent, root, session.id)
        log_event(
            ProjectPaths(root.parent.parent).logs,
            SessionLogEvent.DELETED,
            entity_id=session.id,
            related_id=session.project_id,
            permanent=LifecycleDeletion.PERMANENT,
        )

    @queryable
    def list(self, project: Project, *, include_deleted: bool = CURRENT_RECORDS) -> list[Session]:
        from llm.services.infrastructure.storage import reject_links

        sessions = []
        for directory in project.paths.sessions.iterdir():
            reject_links(directory)
            if directory.is_dir() and not directory.name.startswith(SessionRepository.HIDDEN_PREFIX):
                sessions.append(self.load(project, directory.name))
        return sorted(
            (session for session in sessions if include_deleted or session.status != SessionStatus.DELETED),
            key=lambda session: (session.created_at, session.id),
        )


# 세션 상태를 관리하며 Engine 실행은 RunManager에 맡긴다.
class SessionManager:
    """Session lifecycle only. Call lifecycle mutations while runtime is detached."""

    UNSPECIFIED = None
    CLOSE_WHEN_IDLE = True
    KEEP_OPEN = False
    PROJECT_ENDING = True
    PROJECT_CONTINUES = False
    UNBOUND_AGENTS = "Session participants require the Project Agent owner"
    UNBOUND_ACCESS = "SessionManager needs project_access"
    DELETED = Session.DELETED_ERROR
    PENDING_TURN = "Finish pending work before deleting conversation turns"
    UNBOUND_TURN_REPOSITORY = "Turn deletion requires the shared RunRepository"
    REQUIRED_TURN = "An unfinished Run requires this conversation turn"
    DETACHED_RUNTIME = "Runtime state requires an attached Session"
    ACTIVE_CONVERSATIONS = "Shut down Session runtimes before closing conversation storage"
    INVALID_TITLE = "Session title must be a string"
    ALREADY_BOUND = "SessionManager is already bound to a Project repository"
    REQUIRES_PROJECT_OWNER = "Bind SessionManager through ProjectManager or inject project_access"
    PENDING_MAINTENANCE = "Session maintenance is pending; recover retention before starting runtime"
    ALREADY_ATTACHED = WorkspaceLock.ATTACHED
    ATTACHMENT_ROLLBACK = "runtime_attachment"
    MANAGED_EXECUTION = "Session execution state is managed by RunManager"
    UNBOUND_HISTORY = "Session deletion requires bound Run and Step evidence readers"
    UNBOUND_COMPONENTS = "Session deletion requires bound Component reference readers"
    REFERENCED_HISTORY = "Session history is referenced by a stored Component"
    UNBOUND_QUOTA = "Session quota evidence requires a Run repository"
    REQUIRED_USAGE_RECEIPTS = "Session usage receipts are required by a live Project quota"
    NOT_DELETED = "Session is not deleted"
    INVALID_ABANDONMENT = "abandon_runs requires distinct Run IDs"
    MISSING_REVISION = "Resume abandonment requires expected_revision from delete_turn_plan"
    STALE_PLAN = "Turn deletion plan changed; reload and confirm again"
    RESUMABLE_TURN = "Conversation turn is required by an unfinished resumable Run"
    MISMATCHED_CONFIRMATION = "Confirm exactly the Run IDs reported by delete_turn_plan"
    ATTACHED_RUNTIME = "Session runtime is attached; shut down RunManager first"
    ACTIVE_RUN = "Session has an active Run"

    def __init__(
        self,
        repository: Optional[SessionRepository] = UNSPECIFIED,
        *,
        project_access: Optional[ProjectAccess] = UNSPECIFIED,
        conversations: Union[str, Callable[[Session], Conversation]] = conversation_store,
        context_builder: Optional[ConversationContextBuilder] = UNSPECIFIED,
        conversation_cache_size: int = ProjectConversations.DEFAULT_FILE_CACHE_SIZE,
        runs=UNSPECIFIED,
    ) -> None:
        self.repository = repository if repository is not SessionManager.UNSPECIFIED else SessionRepository()
        self._attached: set[tuple[str, str]] = set()
        self._close_when_idle = SessionManager.KEEP_OPEN
        self._runtime_lock = threading.RLock()
        self.project_access = project_access
        self.conversations = ProjectConversations(
            self._conversation_storage, default=conversations, file_cache_size=conversation_cache_size
        )
        self.context_builder = (
            context_builder
            if context_builder is not SessionManager.UNSPECIFIED
            else ConversationContextBuilder()
        )
        self.run_repository = runs
        self.step_repository = SessionManager.UNSPECIFIED
        self.components = SessionManager.UNSPECIFIED
        self.agents = SessionManager.UNSPECIFIED
        self.config_validator = SessionManager.UNSPECIFIED
        from llm.services.runtime.transfers import TransferRecords

        self.transfers = TransferRecords(self)

    def _validate_agents(self, project, identifiers):
        Session.validate_agents(identifiers)
        if identifiers and self.agents is SessionManager.UNSPECIFIED:
            raise RuntimeError(SessionManager.UNBOUND_AGENTS)
        if identifiers:
            self.agents.repository.load_many(project, identifiers)

    def _conversation_storage(self, session: Session) -> Optional[str]:
        """오래된 핸들의 복사본 대신 소유 프로젝트의 현재 선택을 조회한다."""
        return self._owner(session, allow_deleted=ProjectAccess.INCLUDING_DELETED).conversation_storage

    def _discard_conversation(self, session: Session) -> None:
        """상위 서비스가 영구 삭제를 완료한 뒤 선택적 저장소 정리 계약을 호출한다."""
        discard = getattr(self.conversations, ProjectConversations.DISCARD_METHOD, SessionManager.UNSPECIFIED)
        if callable(discard):
            after_commit(lambda: discard(session))

    def _owner(self, session: Session, *, allow_deleted: bool = ProjectAccess.ALLOW_DELETED) -> Project:
        if self.project_access is SessionManager.UNSPECIFIED:
            raise RuntimeError(SessionManager.UNBOUND_ACCESS)
        project = self.project_access.load(session.project_id, allow_deleted=allow_deleted)
        expected = self.repository.paths(project, session.id)
        if session.paths.root.absolute() != expected.root.absolute():
            raise ValueError(SessionRepository.PATH_MISMATCH)
        return project

    def _turn_deletion(self, session: Session, request_id: str):
        """조회와 변경이 같은 턴/재개 참조를 계산한다. 호출자는 workspace 잠금을 소유한다."""
        from llm.services.history.turns import conversation_turns, ConversationTurns

        if session.status == SessionStatus.DELETED:
            raise ValueError(SessionManager.DELETED)
        store = self.conversations(session)
        messages = store.list()
        if any(m.status in Message.PENDING_STATES for m in messages):
            raise ValueError(SessionManager.PENDING_TURN)
        group = next(
            (g for g in conversation_turns(messages) if ConversationTurns.request(g).id == request_id), SessionManager.UNSPECIFIED
        )
        if group is SessionManager.UNSPECIFIED:
            raise ValueError(ConversationTurns.MISSING_TURN)
        if self.run_repository is SessionManager.UNSPECIFIED:
            raise RuntimeError(SessionManager.UNBOUND_TURN_REPOSITORY)
        self.transfers.require_unreferenced(self._owner(session), session)
        identifiers = {m.id for m in group}
        blockers, runs, snapshots = [], [], []
        for run, protected, digest in self.run_repository.resume_references(session):
            matched = sorted(identifiers & protected)
            if not matched:
                continue
            runs.append(run)
            blockers.append(
                Diagnostic(
                    PlanBlockerCode.RESUME_REQUIRED,
                    SessionManager.REQUIRED_TURN,
                    source=ResourceRef(
                        ResourceKind.RUN,
                        run.id,
                        project_id=session.project_id,
                        session_id=session.id,
                        run_id=run.id,
                    ),
                    details=ResumeTurnDetails(str(run.status), run.engine, matched).to_dict(),
                )
            )
            snapshots.append(
                TurnResumeEvidence(
                    record(run),
                    digest,
                    [v.to_dict() for v in self.run_repository.interaction_views(session, run, store)],
                ).to_dict()
            )
        source = ResourceRef(
            ResourceKind.MESSAGE, request_id, project_id=session.project_id, session_id=session.id
        )
        revision = revision_token(
            TurnDeletionEvidence(source.to_dict(), [record(m) for m in group], snapshots).to_dict()
        )
        return store, group, TurnDeletionPlan(source, [m.id for m in group], blockers, revision), runs

    @workspace_locked
    def _save_runtime(self, session: Session) -> None:
        """Internal RunManager state transitions; public save edits configuration only."""
        self._owner(session)
        if (session.project_id, session.id) not in self._attached:
            raise ValueError(SessionManager.DETACHED_RUNTIME)
        current = self.repository.reload(session)
        if current.status == SessionStatus.DELETED:
            raise ValueError(SessionManager.DELETED)
        # 오래 유지된 런타임 스냅샷이 UI의 제목/메타데이터 수정을 덮어쓰지 않는다.
        session.title, session.metadata = current.title, deepcopy(current.metadata)
        self.repository.save(session)

    # 공개 API
    def bind_config_validator(self, validator) -> None:
        """실행 없이 설정을 검사하는 백엔드 공통 검증기를 연결한다."""
        ConfigurationValidator.require(validator)
        self.config_validator = validator

    @property
    def results(self):
        from llm.services.results import RunResultQuery

        return RunResultQuery(self, self.run_repository)

    def close_conversations(self, *, when_idle: bool = KEEP_OPEN) -> None:
        """소유한 메모리/파일 캐시만 해제한다. 주입 팩토리의 수명은 호출자가 소유한다."""
        with self._runtime_lock:
            if self._attached:
                if when_idle:
                    self._close_when_idle = SessionManager.CLOSE_WHEN_IDLE
                    return
                raise RuntimeError(SessionManager.ACTIVE_CONVERSATIONS)
            self.conversations.clear_owned()

    @workspace_locked
    def update_details(self, session: Session, *, title=UNSPECIFIED, metadata=UNSPECIFIED) -> None:
        """실행 중에도 제목/메타데이터만 최신 상태에 합쳐 저장한다. 설정은 변경하지 않는다."""
        self._owner(session)
        current = self.repository.reload(session)
        if current.status == SessionStatus.DELETED:
            raise ValueError(SessionManager.DELETED)
        if title is not SessionManager.UNSPECIFIED:
            if not isinstance(title, str):
                raise TypeError(SessionManager.INVALID_TITLE)
            current.title = title
        if metadata is not SessionManager.UNSPECIFIED:
            JsonObject.validate(metadata)
            current.metadata = deepcopy(metadata)
        self.repository.save(current)

    @property
    def ownership(self):
        if self.project_access is SessionManager.UNSPECIFIED:
            raise RuntimeError(SessionManager.UNBOUND_ACCESS)
        return self.project_access.repository.ownership

    def bind_project_access(self, access: ProjectAccess) -> None:
        if (
            self.project_access is not SessionManager.UNSPECIFIED
            and self.project_access.repository is not access.repository
        ):
            raise ValueError(SessionManager.ALREADY_BOUND)
        self.project_access = access

    def require_project(
        self, project: Project, *, allow_deleted: bool = ProjectAccess.ALLOW_DELETED
    ) -> Project:
        if self.project_access is SessionManager.UNSPECIFIED:
            raise RuntimeError(SessionManager.REQUIRES_PROJECT_OWNER)
        return self.project_access.require(project, allow_deleted=allow_deleted)

    @workspace_locked
    def attach_runtime(self, session: Session) -> None:
        self._owner(session)
        if HistoryMaintenanceStorage.pending(session):
            raise ValueError(SessionManager.PENDING_MAINTENANCE)
        with self._runtime_lock:
            key = (session.project_id, session.id)
            if key in self._attached:
                raise ValueError(SessionManager.ALREADY_ATTACHED)
            self.ownership.claim_session(key)
            self._attached.add(key)
            transaction = current_transaction()
            if transaction is not SessionManager.UNSPECIFIED:

                def undo_attachment():
                    with self._runtime_lock:
                        if key in self._attached:
                            self._attached.remove(key)
                            self.ownership.release_session(key)

                transaction.on_error((SessionManager.ATTACHMENT_ROLLBACK, id(self), key), undo_attachment)

    @workspace_locked
    def detach_runtime(self, session: Session) -> None:
        with self._runtime_lock:
            key = (session.project_id, session.id)
            if key in self._attached:
                self._attached.remove(key)
                self.ownership.release_session(key)
            if not self._attached and self._close_when_idle:
                self._close_when_idle = SessionManager.KEEP_OPEN
                self.conversations.clear_owned()

    @workspace_locked
    def initialize(self, project: Project) -> None:
        project = self.require_project(project)
        self.repository.initialize(project)

    @workspace_locked
    def create(
        self,
        project: Project,
        title: str,
        *,
        config: Optional[dict] = UNSPECIFIED,
        agent_ids=UNSET,
    ) -> Session:
        project = self.require_project(project)
        identifiers = [] if agent_ids is UNSET else deepcopy(agent_ids)
        self._validate_agents(project, identifiers)
        ProjectConfig.validate_session(config if config is not SessionManager.UNSPECIFIED else {})
        config = deepcopy(config) if config is not SessionManager.UNSPECIFIED else {}
        if self.config_validator is not SessionManager.UNSPECIFIED:
            self.config_validator(project.config, session_config=config)
        self.initialize(project)
        session_id = new_id()
        session = Session(
            session_id,
            project.id,
            title,
            self.repository.paths(project, session_id),
            config=config,
            agent_ids=identifiers,
        )
        self.repository.create(session)
        log_event(session.paths.logs, SessionLogEvent.CREATED, entity_id=session.id, related_id=project.id)
        return session

    @workspace_locked
    def save(self, session: Session) -> None:
        project = self._owner(session)
        self._validate_agents(project, session.agent_ids)
        if self.config_validator is not SessionManager.UNSPECIFIED:
            self.config_validator(project.config, session_config=session.config)
        current = self.require_inactive(session)
        if current.status == SessionStatus.DELETED:
            raise ValueError(SessionManager.DELETED)
        if session.status != current.status or session.active_run_ids != current.active_run_ids:
            raise ValueError(SessionManager.MANAGED_EXECUTION)
        current.title = session.title
        current.metadata = deepcopy(session.metadata)
        current.config = deepcopy(session.config)
        current.agent_ids = deepcopy(session.agent_ids)
        self.repository.save(current)

    @workspace_locked
    def load(self, project: Project, session_id: str) -> Session:
        project = self.require_project(project, allow_deleted=ProjectAccess.INCLUDING_DELETED)
        session = self.repository.load(project, session_id)
        self._validate_agents(project, session.agent_ids)
        return session

    @workspace_locked
    @queryable
    def list(
        self, project: Project, *, include_deleted: bool = SessionRepository.CURRENT_RECORDS
    ) -> list[Session]:
        project = self.require_project(project, allow_deleted=ProjectAccess.INCLUDING_DELETED)
        sessions = self.repository.list(project, include_deleted=include_deleted)
        for session in sessions:
            self._validate_agents(project, session.agent_ids)
        return sessions

    @workspace_locked
    def validate_history(self, session: Session):
        """Validate inactive history through its bound child owners before any pruning."""
        current = self.require_inactive(session)
        if (
            self.run_repository is SessionManager.UNSPECIFIED
            or self.step_repository is SessionManager.UNSPECIFIED
        ):
            raise ValueError(SessionManager.UNBOUND_HISTORY)
        messages = self.conversations(current).list()
        from llm.services.runtime.operations import OperationRepository

        OperationRepository().validate_backup(current)
        self.transfers.validate_session(
            self._owner(current, allow_deleted=ProjectAccess.INCLUDING_DELETED), current, messages
        )
        runs = self.run_repository.read_backup(current)
        from llm.services.runtime.requests import RequestAdmission

        RequestAdmission.history(
            messages,
            runs,
            project_id=current.project_id,
            session_id=current.id,
            persistent=self._owner(
                current, allow_deleted=ProjectAccess.INCLUDING_DELETED
            ).conversation_storage
            == ConversationStorage.FILE,
        )
        for run in runs:
            self.step_repository.read_backup(run)
        return current, messages, runs

    @workspace_locked
    def validate_removal(self, session: Session, *, project_ending=PROJECT_CONTINUES) -> Session:
        """Parent deletion requires the same child-owner evidence as ordinary reads."""
        current, messages, runs = self.validate_history(session)
        if not project_ending:
            self.transfers.require_unreferenced(
                self._owner(current, allow_deleted=ProjectAccess.INCLUDING_DELETED), current
            )
            if self.components is SessionManager.UNSPECIFIED:
                raise ValueError(SessionManager.UNBOUND_COMPONENTS)
            refs = HistoryReferenceSet(
                self.components.history_references(
                    self._owner(current, allow_deleted=ProjectAccess.INCLUDING_DELETED)
                )
            )
            if (
                current.id in refs.session_ids
                or any(run.id in refs.run_ids for run in runs)
                or any(message.id in refs.message_ids for message in messages)
            ):
                raise ValueError(SessionManager.REFERENCED_HISTORY)
        return current

    @workspace_locked
    def delete(self, session: Session, *, permanent: bool = LifecycleDeletion.DEFAULT) -> None:
        """Mark deleted by default; permanent=True removes history and artifacts."""
        LifecycleDeletion.validate(permanent)
        current = self.require_inactive(session)
        self.transfers.require_unreferenced(
            self._owner(current, allow_deleted=ProjectAccess.INCLUDING_DELETED), current
        )
        if permanent:
            self.validate_removal(current)
            from llm.services.runtime.usage import project_usage_policies, protects_project_usage

            project = self._owner(current, allow_deleted=ProjectAccess.INCLUDING_DELETED)
            policies = project_usage_policies(project, self)
            if any(policy.project.active for policy in policies):
                if self.run_repository is SessionManager.UNSPECIFIED:
                    raise ValueError(SessionManager.UNBOUND_QUOTA)
                entries = self.run_repository.completion_history(current)
                if protects_project_usage(entries, policies):
                    raise ValueError(SessionManager.REQUIRED_USAGE_RECEIPTS)
            self.repository.delete(current)
            self._discard_conversation(current)
        else:
            current.status = SessionStatus.DELETED
            self.repository.save(current)
            log_event(
                current.paths.logs,
                SessionLogEvent.DELETED,
                entity_id=current.id,
                permanent=LifecycleDeletion.DEFAULT,
            )
        session.status = SessionStatus.DELETED

    @workspace_locked
    def restore(self, session: Session) -> None:
        self._owner(session)
        current = self.require_inactive(session)
        if current.status != SessionStatus.DELETED:
            raise ValueError(SessionManager.NOT_DELETED)
        current.status = SessionStatus.IDLE
        self.repository.save(current)
        session.status = SessionStatus.IDLE
        log_event(current.paths.logs, SessionLogEvent.RESTORED, entity_id=current.id)

    @workspace_locked
    def clone(
        self,
        source: Session,
        project: Project,
        *,
        title: Optional[str] = UNSPECIFIED,
        through_message_id: str | None = UNSPECIFIED,
    ) -> Session:
        """Copy conversation/configuration, with no execution history or queue replay."""
        self._owner(source)
        source = self.require_inactive(source)
        if source.status == SessionStatus.DELETED:
            raise ValueError(SessionManager.DELETED)
        destination_project = self.require_project(project)
        if self.config_validator is not SessionManager.UNSPECIFIED:
            self.config_validator(destination_project.config, session_config=source.config)
        messages = self.conversations(source).list()
        self.transfers.validate_session(self._owner(source), source, messages)
        if through_message_id is not SessionManager.UNSPECIFIED:
            from llm.services.history.turns import through_turn

            messages = through_turn(messages, through_message_id)
        clone = self.create(
            project,
            title if title is not SessionManager.UNSPECIFIED else source.title,
            agent_ids=source.agent_ids,
        )
        clone.metadata = deepcopy(source.metadata)
        clone.config = deepcopy(source.config)
        destination = self.conversations(clone)
        messages = self.context_builder.for_clone(messages)
        for message in messages:
            destination.create(message.role, message.content, message.status, metadata=message.metadata)
        self.save(clone)
        log_event(clone.paths.logs, SessionLogEvent.CLONED, entity_id=clone.id, related_id=source.id)
        return clone

    @workspace_locked
    def delete_turn_plan(self, session: Session, request_id: str) -> TurnDeletionPlan:
        """실행을 멈추거나 수정하지 않고 삭제 영향과 CAS revision을 조회한다."""
        self._owner(session)
        return self._turn_deletion(self.repository.reload(session), request_id)[2]

    @workspace_locked
    def checkpoint_plan(self, session: Session, run_id: str):
        self._owner(session)
        current = self.repository.reload(session)
        run = self.run_repository.load(current, run_id)
        return self.run_repository.checkpoint_plan(
            current,
            run,
            self.conversations(current),
            attached=self.ownership.session_attached((current.project_id, current.id)),
        )

    @workspace_locked
    def abandon_resume(self, session: Session, run_id: str, *, expected_revision):
        """대화 삭제 없이 재개만 포기한다. 실행 효과·상태·대화는 유지한다."""
        from llm.core.checkpoints import require_checkpoint_plan, CheckpointAction

        self._owner(session)
        current = self.require_inactive(session)
        run = self.run_repository.load(current, run_id)
        plan = self.run_repository.checkpoint_plan(current, run, self.conversations(current))
        require_checkpoint_plan(plan, expected_revision, action=CheckpointAction.ABANDON)
        self.run_repository.abandon_resume(run)
        activity_event(
            self._owner(current),
            run,
            ActivityEventType.RUN_RESUME_ABANDONED,
            time=ResumeAbandonment.require(run.metadata).abandoned_at,
            status=run.status,
        )

    @workspace_locked
    def delete_checkpoints(self, session: Session, run_id: str, *, expected_revision):
        self._owner(session)
        current = self.require_inactive(session)
        run = self.run_repository.load(current, run_id)
        self.run_repository.delete_checkpoints(
            current, run, self.conversations(current), expected_revision=expected_revision
        )

    @workspace_locked
    def delete_turn(
        self,
        session: Session,
        request_id: str,
        *,
        abandon_runs=(),
        expected_revision=UNSPECIFIED,
    ) -> None:
        """확인된 재개 포기와 대화 논리 삭제를 하나의 트랜잭션으로 저장한다."""
        if (
            not isinstance(abandon_runs, (tuple, list))
            or any(not isinstance(value, str) or not value for value in abandon_runs)
            or len(set(abandon_runs)) != len(abandon_runs)
        ):
            raise ValueError(SessionManager.INVALID_ABANDONMENT)
        if abandon_runs and (not isinstance(expected_revision, str) or not expected_revision):
            raise ValueError(SessionManager.MISSING_REVISION)
        current = self.require_inactive(session)
        store, group, plan, runs = self._turn_deletion(current, request_id)
        if expected_revision is not SessionManager.UNSPECIFIED and expected_revision != plan.revision:
            raise ValueError(SessionManager.STALE_PLAN)
        if runs and not abandon_runs:
            raise ValueError(SessionManager.RESUMABLE_TURN)
        if set(abandon_runs) != {run.id for run in runs}:
            raise ValueError(SessionManager.MISMATCHED_CONFIRMATION)
        for run in runs:
            self.run_repository.abandon_resume(run, request_id)
            activity_event(
                self._owner(current),
                run,
                ActivityEventType.RUN_RESUME_ABANDONED,
                time=ResumeAbandonment.require(run.metadata).abandoned_at,
                status=run.status,
            )
        for message in group:
            store.update_metadata(message.id, Message.deletion_metadata())
        log_event(
            current.paths.logs, SessionLogEvent.CONVERSATION_DELETED, entity_id=request_id, count=len(group)
        )

    @workspace_locked
    def require_inactive(self, session: Session) -> Session:
        self._owner(session, allow_deleted=ProjectAccess.INCLUDING_DELETED)
        if self.ownership.session_attached((session.project_id, session.id)):
            raise ValueError(SessionManager.ATTACHED_RUNTIME)
        # Reload to avoid trusting a stale handle held by a caller.
        current = self.repository.reload(session)
        if current.status == SessionStatus.RUNNING or current.active_run_ids:
            raise ValueError(SessionManager.ACTIVE_RUN)
        return current
