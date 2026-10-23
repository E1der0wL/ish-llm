"""Session의 영속 상태와 수명 주기를 관리한다. SessionRuntime은 실행 전용이며 RunManager가 소유한다. 제목/메타데이터 수정은 실행 설정 변경과 분리한다."""

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

from llm.core.models import ConversationStorage, Message, MessageStatus, Project, ProjectConfig, Run, Session, SessionStatus, new_id
from llm.core.json_data import JsonObject
from llm.core.configuration import UNSET
from llm.core.paths import ProjectPaths, SessionPaths
from llm.core.contracts import Diagnostic, ResourceRef
from llm.core.plans import TurnDeletionPlan
from llm.core.checkpoints import ResumeAbandonment
from llm.services.history.conversation import Conversation, ProjectConversations, conversation_store
from llm.services.history.context import ConversationContextBuilder
from llm.services.lifecycle.access import ProjectAccess
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.activity import activity_event
from llm.services.infrastructure.storage import atomic_json, child, read_json, record, remove_owned_tree, revision_token


@dataclass(slots=True)
class RunRuntime:
    """One Run's preparation, cancellable consumer and mandatory finalizer."""

    run: Run
    task: Optional[asyncio.Task[None]] = None
    execution: Optional[asyncio.Task[None]] = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    interrupt_requested: bool = False

    def interrupt(self):
        """Deliver cancellation once; cleanup owns any still-running work."""
        if not self.interrupt_requested:
            self.interrupt_requested = True
            if self.execution is not None:
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

    def _load(self, paths: SessionPaths, session_id: str, project_id: str) -> Session:
        data = read_domain_record(paths.record)
        if data["id"] != session_id or data["project_id"] != project_id:
            raise ValueError("Session ownership mismatch")
        ProjectConfig.validate_session(data["config"])
        session = Session.from_record(data, paths)
        from llm.services.runtime.operations import OperationRepository
        OperationRepository().has_records(session)
        from llm.services.runtime.transfers import TransferLedger
        TransferLedger.read(session)
        log_event(paths.logs, "session.loaded", entity_id=session_id)
        return session

    def read_backup(self, project):
        """Session 파일/대화만 검증한다. 실행 복구나 로그 쓰기를 하지 않는다."""
        from llm.services.history.conversation import ConversationStore
        sessions = []
        for folder in project.paths.sessions.iterdir():
            if not folder.is_dir():
                continue
            data = read_domain_record(SessionPaths(folder).record)
            if child(project.paths.sessions, data["id"]) != folder or data["project_id"] != project.id:
                raise ValueError("Backup Session ownership mismatch")
            session = Session.from_record(data, SessionPaths(folder))
            ProjectConfig.validate_session(session.config)
            if session.status == SessionStatus.RUNNING or session.active_run_ids:
                raise ValueError("Backup contains an active Session")
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
        log_event(project.paths.logs, "sessions.initialized", entity_id=project.id)

    def create(self, session: Session) -> None:
        if session.paths.root.exists():
            raise FileExistsError("Session storage already exists")
        self._write(session, creating=True)

    def save(self, session: Session) -> None:
        self._load(session.paths, session.id, session.project_id)
        self._write(session, creating=False)

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
        log_event(session.paths.logs, "session.saved", entity_id=session.id, status=session.status)

    def load(self, project: Project, session_id: str) -> Session:
        return self._load(self.paths(project, session_id), session_id, project.id)

    def reload(self, session: Session) -> Session:
        return self._load(session.paths, session.id, session.project_id)

    def delete(self, session: Session) -> None:
        """Remove the Session subtree only after validating layout and ownership."""
        root = session.paths.root
        if (root.name != session.id or root.parent.name != "sessions"
                or root.parent.parent.name != session.project_id):
            raise ValueError("Session path ownership mismatch")
        self.reload(session)
        remove_owned_tree(root.parent, root, session.id)
        log_event(ProjectPaths(root.parent.parent).logs, "session.deleted",
                  entity_id=session.id, related_id=session.project_id, permanent=True)

    @queryable
    def list(self, project: Project, *, include_deleted: bool = False) -> list[Session]:
        from llm.services.infrastructure.storage import reject_links
        sessions = []
        for directory in project.paths.sessions.iterdir():
            reject_links(directory)
            if directory.is_dir() and not directory.name.startswith("."):
                sessions.append(self.load(project, directory.name))
        return sorted((session for session in sessions
                       if include_deleted or session.status != SessionStatus.DELETED),
                      key=lambda session: (session.created_at, session.id))


# 세션 상태를 관리하며 Engine 실행은 RunManager에 맡긴다.
class SessionManager:
    """Session lifecycle only. Call lifecycle mutations while runtime is detached."""

    def __init__(self, repository: Optional[SessionRepository] = None, *,
                 project_access: Optional[ProjectAccess] = None,
                 conversations: Union[str, Callable[[Session], Conversation]] = conversation_store,
                 context_builder: Optional[ConversationContextBuilder] = None,
                 conversation_cache_size: int = ProjectConversations.DEFAULT_FILE_CACHE_SIZE,
                 runs=None) -> None:
        self.repository = repository if repository is not None else SessionRepository()
        self._attached: set[tuple[str, str]] = set()
        self._close_when_idle = False
        self._runtime_lock = threading.RLock()
        self.project_access = project_access
        self.conversations = ProjectConversations(self._conversation_storage, default=conversations,
                                                  file_cache_size=conversation_cache_size)
        self.context_builder = context_builder if context_builder is not None else ConversationContextBuilder()
        self.run_repository = runs
        self.step_repository = None
        self.components = None
        self.agents = None
        self.config_validator = None
        from llm.services.runtime.transfers import TransferRecords
        self.transfers = TransferRecords(self)

    def _validate_agents(self, project, identifiers):
        Session.validate_agents(identifiers)
        if identifiers and self.agents is None:
            raise RuntimeError("Session participants require the Project Agent owner")
        for identifier in identifiers:
            self.agents.repository.load(project, identifier)

    def _conversation_storage(self, session: Session) -> Optional[str]:
        """오래된 핸들의 복사본 대신 소유 프로젝트의 현재 선택을 조회한다."""
        return self._owner(session, allow_deleted=True).conversation_storage

    def _discard_conversation(self, session: Session) -> None:
        """상위 서비스가 영구 삭제를 완료한 뒤 선택적 저장소 정리 계약을 호출한다."""
        discard = getattr(self.conversations, "discard", None)
        if callable(discard):
            after_commit(lambda: discard(session))

    def _owner(self, session: Session, *, allow_deleted: bool = False) -> Project:
        if self.project_access is None:
            raise RuntimeError("SessionManager needs project_access")
        project = self.project_access.load(session.project_id, allow_deleted=allow_deleted)
        expected = self.repository.paths(project, session.id)
        if session.paths.root.absolute() != expected.root.absolute():
            raise ValueError("Session path ownership mismatch")
        return project

    def _turn_deletion(self, session: Session, request_id: str):
        """조회와 변경이 같은 턴/재개 참조를 계산한다. 호출자는 workspace 잠금을 소유한다."""
        from llm.services.history.turns import conversation_turns
        if session.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        store = self.conversations(session)
        messages = store.list()
        if any(m.status in (MessageStatus.QUEUED, MessageStatus.STREAMING) for m in messages):
            raise ValueError("Finish pending work before deleting conversation turns")
        group = next((g for g in conversation_turns(messages) if g[0].id == request_id), None)
        if group is None:
            raise ValueError("Conversation turn does not exist")
        if self.run_repository is None:
            raise RuntimeError("Turn deletion requires the shared RunRepository")
        self.transfers.require_unreferenced(self._owner(session), session)
        identifiers = {m.id for m in group}
        blockers, runs, snapshots = [], [], []
        for run, protected, digest in self.run_repository.resume_references(session):
            matched = sorted(identifiers & protected)
            if not matched:
                continue
            runs.append(run)
            blockers.append(Diagnostic(PlanBlockerCode.RESUME_REQUIRED, "An unfinished Run requires this conversation turn",
                source=ResourceRef(ResourceKind.RUN, run.id, project_id=session.project_id, session_id=session.id, run_id=run.id),
                details={"status": str(run.status), "engine": run.engine, "message_ids": matched}))
            snapshots.append({"run": record(run), "checkpoint": digest,
                "interactions": [v.to_dict() for v in self.run_repository.interaction_views(session, run, store)]})
        source = ResourceRef(ResourceKind.MESSAGE, request_id, project_id=session.project_id, session_id=session.id)
        revision = revision_token({"source": source.to_dict(), "messages": [record(m) for m in group],
                                   "dependencies": snapshots})
        return store, group, TurnDeletionPlan(source, [m.id for m in group], blockers, revision), runs

    @workspace_locked
    def _save_runtime(self, session: Session) -> None:
        """Internal RunManager state transitions; public save edits configuration only."""
        self._owner(session)
        if (session.project_id, session.id) not in self._attached:
            raise ValueError("Runtime state requires an attached Session")
        current = self.repository.reload(session)
        if current.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        # 오래 유지된 런타임 스냅샷이 UI의 제목/메타데이터 수정을 덮어쓰지 않는다.
        session.title, session.metadata = current.title, deepcopy(current.metadata)
        self.repository.save(session)

    # 공개 API
    def bind_config_validator(self, validator) -> None:
        """실행 없이 설정을 검사하는 백엔드 공통 검증기를 연결한다."""
        if validator is not None and not callable(validator):
            raise TypeError("Configuration validator must be callable")
        self.config_validator = validator

    @property
    def results(self):
        from llm.services.results import RunResultQuery
        return RunResultQuery(self, self.run_repository)

    def close_conversations(self, *, when_idle: bool = False) -> None:
        """소유한 메모리/파일 캐시만 해제한다. 주입 팩토리의 수명은 호출자가 소유한다."""
        with self._runtime_lock:
            if self._attached:
                if when_idle:
                    self._close_when_idle = True
                    return
                raise RuntimeError("Shut down Session runtimes before closing conversation storage")
            self.conversations.clear_owned()

    @workspace_locked
    def update_details(self, session: Session, *, title=None, metadata=None) -> None:
        """실행 중에도 제목/메타데이터만 최신 상태에 합쳐 저장한다. 설정은 변경하지 않는다."""
        self._owner(session)
        current = self.repository.reload(session)
        if current.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        if title is not None:
            if not isinstance(title, str):
                raise TypeError("Session title must be a string")
            current.title = title
        if metadata is not None:
            JsonObject.validate(metadata)
            current.metadata = deepcopy(metadata)
        self.repository.save(current)

    @property
    def ownership(self):
        if self.project_access is None:
            raise RuntimeError("SessionManager needs project_access")
        return self.project_access.repository.ownership

    def bind_project_access(self, access: ProjectAccess) -> None:
        if self.project_access is not None and self.project_access.repository is not access.repository:
            raise ValueError("SessionManager is already bound to a Project repository")
        self.project_access = access

    def require_project(self, project: Project, *, allow_deleted: bool = False) -> Project:
        if self.project_access is None:
            raise RuntimeError("Bind SessionManager through ProjectManager or inject project_access")
        return self.project_access.require(project, allow_deleted=allow_deleted)

    @workspace_locked
    def attach_runtime(self, session: Session) -> None:
        self._owner(session)
        if any((session.paths.state / "maintenance").glob("*.json")):
            raise ValueError("Session maintenance is pending; recover retention before starting runtime")
        with self._runtime_lock:
            key = (session.project_id, session.id)
            if key in self._attached:
                raise ValueError("Session already has an attached runtime")
            self.ownership.claim_session(key)
            self._attached.add(key)
            transaction = current_transaction()
            if transaction is not None:
                def undo_attachment():
                    with self._runtime_lock:
                        if key in self._attached:
                            self._attached.remove(key)
                            self.ownership.release_session(key)
                transaction.on_error(("runtime_attachment", id(self), key), undo_attachment)

    @workspace_locked
    def detach_runtime(self, session: Session) -> None:
        with self._runtime_lock:
            key = (session.project_id, session.id)
            if key in self._attached:
                self._attached.remove(key)
                self.ownership.release_session(key)
            if not self._attached and self._close_when_idle:
                self._close_when_idle = False
                self.conversations.clear_owned()

    @workspace_locked
    def initialize(self, project: Project) -> None:
        project = self.require_project(project)
        self.repository.initialize(project)

    @workspace_locked
    def create(self, project: Project, title: str, *,
               config: Optional[dict] = None, agent_ids=UNSET) -> Session:
        project = self.require_project(project)
        identifiers = [] if agent_ids is UNSET else deepcopy(agent_ids)
        self._validate_agents(project, identifiers)
        ProjectConfig.validate_session(config if config is not None else {})
        config = deepcopy(config) if config is not None else {}
        if self.config_validator is not None:
            self.config_validator(project.config, session_config=config)
        self.initialize(project)
        session_id = new_id()
        session = Session(session_id, project.id, title, self.repository.paths(project, session_id),
                    config=config, agent_ids=identifiers)
        self.repository.create(session)
        log_event(session.paths.logs, "session.created", entity_id=session.id,
                  related_id=project.id)
        return session

    @workspace_locked
    def save(self, session: Session) -> None:
        project = self._owner(session)
        self._validate_agents(project, session.agent_ids)
        if self.config_validator is not None:
            self.config_validator(project.config, session_config=session.config)
        current = self.require_inactive(session)
        if current.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        if session.status != current.status or session.active_run_ids != current.active_run_ids:
            raise ValueError("Session execution state is managed by RunManager")
        current.title = session.title
        current.metadata = deepcopy(session.metadata)
        current.config = deepcopy(session.config)
        current.agent_ids = deepcopy(session.agent_ids)
        self.repository.save(current)

    @workspace_locked
    def load(self, project: Project, session_id: str) -> Session:
        project = self.require_project(project, allow_deleted=True)
        session = self.repository.load(project, session_id)
        self._validate_agents(project, session.agent_ids)
        return session

    @workspace_locked
    @queryable
    def list(self, project: Project, *, include_deleted: bool = False) -> list[Session]:
        project = self.require_project(project, allow_deleted=True)
        sessions = self.repository.list(project, include_deleted=include_deleted)
        for session in sessions:
            self._validate_agents(project, session.agent_ids)
        return sessions

    @workspace_locked
    def validate_history(self, session: Session):
        """Validate inactive history through its bound child owners before any pruning."""
        current = self.require_inactive(session)
        if self.run_repository is None or self.step_repository is None:
            raise ValueError("Session deletion requires bound Run and Step evidence readers")
        messages = self.conversations(current).list()
        from llm.services.runtime.operations import OperationRepository
        OperationRepository().validate_backup(current)
        self.transfers.validate_session(self._owner(current, allow_deleted=True), current, messages)
        runs = self.run_repository.read_backup(current)
        from llm.services.runtime.requests import RequestAdmission
        RequestAdmission.history(messages, runs, project_id=current.project_id,
            session_id=current.id, persistent=self._owner(current, allow_deleted=True).conversation_storage == ConversationStorage.FILE)
        for run in runs:
            self.step_repository.read_backup(run)
        return current, messages, runs

    @workspace_locked
    def validate_removal(self, session: Session, *, project_ending=False) -> Session:
        """Parent deletion requires the same child-owner evidence as ordinary reads."""
        current, messages, runs = self.validate_history(session)
        if not project_ending:
            self.transfers.require_unreferenced(self._owner(current, allow_deleted=True), current)
            if self.components is None:
                raise ValueError("Session deletion requires bound Component reference readers")
            refs = self.components.history_references(self._owner(current, allow_deleted=True))
            if (current.id in refs["session_ids"] or any(run.id in refs["run_ids"] for run in runs)
                    or any(message.id in refs["message_ids"] for message in messages)):
                raise ValueError("Session history is referenced by a stored Component")
        return current

    @workspace_locked
    def delete(self, session: Session, *, permanent: bool = False) -> None:
        """Mark deleted by default; permanent=True removes history and artifacts."""
        if type(permanent) is not bool:
            raise TypeError("permanent must be a bool")
        current = self.require_inactive(session)
        self.transfers.require_unreferenced(self._owner(current, allow_deleted=True), current)
        if permanent:
            self.validate_removal(current)
            from llm.services.runtime.usage import has_limit, project_usage_policies, protects_project_usage
            project = self._owner(current, allow_deleted=True)
            policies = project_usage_policies(project, self)
            if any(has_limit(policy, project=True) for policy in policies):
                if self.run_repository is None:
                    raise ValueError("Session quota evidence requires a Run repository")
                entries = self.run_repository.completion_history(current)
                if protects_project_usage(entries, policies):
                    raise ValueError("Session usage receipts are required by a live Project quota")
            self.repository.delete(current)
            self._discard_conversation(current)
        else:
            current.status = SessionStatus.DELETED
            self.repository.save(current)
            log_event(current.paths.logs, "session.deleted", entity_id=current.id,
                      permanent=False)
        session.status = SessionStatus.DELETED

    @workspace_locked
    def restore(self, session: Session) -> None:
        self._owner(session)
        current = self.require_inactive(session)
        if current.status != SessionStatus.DELETED:
            raise ValueError("Session is not deleted")
        current.status = SessionStatus.IDLE
        self.repository.save(current)
        session.status = SessionStatus.IDLE
        log_event(current.paths.logs, "session.restored", entity_id=current.id)

    @workspace_locked
    def clone(self, source: Session, project: Project, *, title: Optional[str] = None,
              through_message_id: str | None = None) -> Session:
        """Copy conversation/configuration, with no execution history or queue replay."""
        self._owner(source)
        source = self.require_inactive(source)
        if source.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        destination_project = self.require_project(project)
        if self.config_validator is not None:
            self.config_validator(destination_project.config, session_config=source.config)
        messages = self.conversations(source).list()
        self.transfers.validate_session(self._owner(source), source, messages)
        if through_message_id is not None:
            from llm.services.history.turns import through_turn
            messages = through_turn(messages, through_message_id)
        clone = self.create(project, title if title is not None else source.title,
                            agent_ids=source.agent_ids)
        clone.metadata = deepcopy(source.metadata)
        clone.config = deepcopy(source.config)
        destination = self.conversations(clone)
        messages = self.context_builder.for_clone(messages)
        for message in messages:
            destination.create(message.role, message.content, message.status,
                               metadata=message.metadata)
        self.save(clone)
        log_event(clone.paths.logs, "session.cloned", entity_id=clone.id,
                  related_id=source.id)
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
        return self.run_repository.checkpoint_plan(current, run, self.conversations(current),
            attached=self.ownership.session_attached((current.project_id, current.id)))

    @workspace_locked
    def abandon_resume(self, session: Session, run_id: str, *, expected_revision):
        """대화 삭제 없이 재개만 포기한다. 실행 효과·상태·대화는 유지한다."""
        from llm.core.checkpoints import require_checkpoint_plan
        self._owner(session)
        current = self.require_inactive(session)
        run = self.run_repository.load(current, run_id)
        plan = self.run_repository.checkpoint_plan(current, run, self.conversations(current))
        require_checkpoint_plan(plan, expected_revision, action="abandon")
        self.run_repository.abandon_resume(run)
        activity_event(self._owner(current), run, ActivityEventType.RUN_RESUME_ABANDONED,
            time=ResumeAbandonment.require(run.metadata).abandoned_at, status=run.status)

    @workspace_locked
    def delete_checkpoints(self, session: Session, run_id: str, *, expected_revision):
        self._owner(session)
        current = self.require_inactive(session)
        run = self.run_repository.load(current, run_id)
        self.run_repository.delete_checkpoints(current, run, self.conversations(current),
                                              expected_revision=expected_revision)

    @workspace_locked
    def delete_turn(self, session: Session, request_id: str, *, abandon_runs=(), expected_revision=None) -> None:
        """확인된 재개 포기와 대화 논리 삭제를 하나의 트랜잭션으로 저장한다."""
        if (not isinstance(abandon_runs, (tuple, list))
                or any(not isinstance(value, str) or not value for value in abandon_runs)
                or len(set(abandon_runs)) != len(abandon_runs)):
            raise ValueError("abandon_runs requires distinct Run IDs")
        if abandon_runs and (not isinstance(expected_revision, str) or not expected_revision):
            raise ValueError("Resume abandonment requires expected_revision from delete_turn_plan")
        current = self.require_inactive(session)
        store, group, plan, runs = self._turn_deletion(current, request_id)
        if expected_revision is not None and expected_revision != plan.revision:
            raise ValueError("Turn deletion plan changed; reload and confirm again")
        if runs and not abandon_runs:
            raise ValueError("Conversation turn is required by an unfinished resumable Run")
        if set(abandon_runs) != {run.id for run in runs}:
            raise ValueError("Confirm exactly the Run IDs reported by delete_turn_plan")
        for run in runs:
            self.run_repository.abandon_resume(run, request_id)
            activity_event(self._owner(current), run, ActivityEventType.RUN_RESUME_ABANDONED,
                time=ResumeAbandonment.require(run.metadata).abandoned_at, status=run.status)
        for message in group:
            store.update_metadata(message.id, Message.deletion_metadata())
        log_event(current.paths.logs, "conversation.deleted", entity_id=request_id, count=len(group))

    @workspace_locked
    def require_inactive(self, session: Session) -> Session:
        self._owner(session, allow_deleted=True)
        if self.ownership.session_attached((session.project_id, session.id)):
            raise ValueError("Session runtime is attached; shut down RunManager first")
        # Reload to avoid trusting a stale handle held by a caller.
        current = self.repository.reload(session)
        if current.status == SessionStatus.RUNNING or current.active_run_ids:
            raise ValueError("Session has an active Run")
        return current
