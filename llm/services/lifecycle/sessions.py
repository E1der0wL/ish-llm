"""Session의 영속 상태와 수명 주기를 관리한다. SessionRuntime은 실행 전용이며 RunManager가 소유한다. 제목/메타데이터 수정은 실행 설정 변경과 분리한다."""

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

from llm.core.models import Project, ProjectConfig, Session, SessionStatus, new_id
from llm.core.paths import ProjectPaths, SessionPaths
from llm.services.history.conversation import Conversation, ProjectConversations, conversation_store
from llm.services.history.context import ConversationContextBuilder
from llm.services.lifecycle.access import ProjectAccess
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import atomic_json, child, read_json, record, remove_owned_tree


@dataclass(slots=True)
class SessionRuntime:
    """Runtime-only state, owned and scheduled exclusively by RunManager."""

    project: Project
    session: Session
    queue: asyncio.Queue[Optional[str]] = field(default_factory=asyncio.Queue)
    worker: Optional[asyncio.Task[None]] = None
    execution: Optional[asyncio.Task[None]] = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False
    preparing: bool = False
    interrupt_requested: bool = False


# 프로젝트 아래의 Session 메타데이터를 읽고 쓴다.
class SessionRepository:
    """Session metadata and paths beneath the owning Project's sessions root."""

    def _load(self, paths: SessionPaths, session_id: str, project_id: str) -> Session:
        data = read_domain_record(paths.root / "session.json")
        if data["id"] != session_id or data["project_id"] != project_id:
            raise ValueError("Session ownership mismatch")
        ProjectConfig.validate_session(data["config"])
        log_event(paths.logs, "session.loaded", entity_id=session_id)
        return Session(**{**data, "status": SessionStatus(data["status"]), "paths": paths})

    def read_backup(self, project):
        """Session 파일/대화만 검증한다. 실행 복구나 로그 쓰기를 하지 않는다."""
        from llm.services.history.conversation import ConversationStore
        sessions = []
        for folder in project.paths.sessions.iterdir():
            if not folder.is_dir():
                continue
            data = read_domain_record(folder / "session.json")
            if child(project.paths.sessions, data["id"]) != folder or data["project_id"] != project.id:
                raise ValueError("Backup Session ownership mismatch")
            session = Session(**{**data, "paths": SessionPaths(folder), "status": SessionStatus(data["status"])})
            ProjectConfig.validate_session(session.config)
            if session.status == SessionStatus.RUNNING or session.current_run_id is not None:
                raise ValueError("Backup contains an active Session")
            ConversationStore(session.paths.conversation).list()
            sessions.append(session)
        return sessions

    # 공개 API
    def paths(self, project: Project, session_id: str) -> SessionPaths:
        return SessionPaths(child(project.paths.sessions, session_id))

    def initialize(self, project: Project) -> None:
        make_directory(project.paths.sessions)
        log_event(project.paths.logs, "sessions.initialized", entity_id=project.id)

    def save(self, session: Session) -> None:
        ProjectConfig.validate_session(session.config)
        atomic_domain_json(session.paths.root / "session.json", record(session))
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
        sessions = [self.load(project, path.parent.name)
                 for path in project.paths.sessions.glob("*/session.json")]
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
                 conversation_cache_size: int = 32,
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
        self.configuration_validator = None

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
    def bind_configuration_validator(self, validator) -> None:
        """실행 없이 설정을 검사하는 백엔드 공통 검증기를 연결한다."""
        if validator is not None and not callable(validator):
            raise TypeError("Configuration validator must be callable")
        self.configuration_validator = validator

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
            ProjectConfig.validate_settings(metadata)
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
               config: Optional[dict] = None) -> Session:
        project = self.require_project(project)
        ProjectConfig.validate_session(config if config is not None else {})
        settings = ProjectConfig.merge(project.config.session_defaults, config or {})
        if self.configuration_validator is not None:
            self.configuration_validator(project.config, session_config=settings)
        self.initialize(project)
        session_id = new_id()
        session = Session(session_id, project.id, title, self.repository.paths(project, session_id),
                    config=settings)
        self.repository.save(session)
        log_event(session.paths.logs, "session.created", entity_id=session.id,
                  related_id=project.id)
        return session

    @workspace_locked
    def save(self, session: Session) -> None:
        project = self._owner(session)
        if self.configuration_validator is not None:
            self.configuration_validator(project.config, session_config=session.config)
        current = self.require_inactive(session)
        if current.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        if session.status != current.status or session.current_run_id != current.current_run_id:
            raise ValueError("Session execution state is managed by RunManager")
        current.title = session.title
        current.metadata = deepcopy(session.metadata)
        current.config = deepcopy(session.config)
        self.repository.save(current)

    @workspace_locked
    def load(self, project: Project, session_id: str) -> Session:
        project = self.require_project(project, allow_deleted=True)
        return self.repository.load(project, session_id)

    @workspace_locked
    @queryable
    def list(self, project: Project, *, include_deleted: bool = False) -> list[Session]:
        project = self.require_project(project, allow_deleted=True)
        return self.repository.list(project, include_deleted=include_deleted)

    @workspace_locked
    def delete(self, session: Session, *, permanent: bool = False) -> None:
        """Mark deleted by default; permanent=True removes history and artifacts."""
        if type(permanent) is not bool:
            raise TypeError("permanent must be a bool")
        current = self.require_inactive(session)
        if permanent:
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
        if self.configuration_validator is not None:
            self.configuration_validator(destination_project.config, session_config=source.config)
        messages = self.conversations(source).list()
        if through_message_id is not None:
            from llm.services.history.turns import through_turn
            messages = through_turn(messages, through_message_id)
        clone = self.create(project, title if title is not None else source.title)
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
    def delete_turn(self, session: Session, request_id: str) -> None:
        """Append a soft-deletion marker; execution records remain inspectable."""
        from llm.services.history.turns import conversation_turns
        current = self.require_inactive(session)
        if current.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        store = self.conversations(current)
        messages = store.list()
        if any(m.status in ("queued", "streaming") for m in messages):
            raise ValueError("Finish pending work before deleting conversation turns")
        group = next((g for g in conversation_turns(messages) if g[0].id == request_id), None)
        if group is None:
            raise ValueError("Conversation turn does not exist")
        # paused 원본은 재개 완료 후에도 이력으로 남는다. 상태가 아닌 현재 체크포인트 참조를 보호한다.
        if self.run_repository is None:
            raise RuntimeError("Turn deletion requires the shared RunRepository")
        protected = self.run_repository.resumable_message_ids(current)
        if any(message.id in protected for message in group):
            raise ValueError("Conversation turn is required by an unfinished resumable Run")
        for message in group:
            store.update_metadata(message.id, {"conversation_deleted": True})
        log_event(current.paths.logs, "conversation.deleted", entity_id=request_id, count=len(group))

    @workspace_locked
    def require_inactive(self, session: Session) -> Session:
        self._owner(session, allow_deleted=True)
        if self.ownership.session_attached((session.project_id, session.id)):
            raise ValueError("Session runtime is attached; shut down RunManager first")
        # Reload to avoid trusting a stale handle held by a caller.
        current = self.repository.reload(session)
        if current.status == SessionStatus.RUNNING or current.current_run_id is not None:
            raise ValueError("Session has an active Run")
        return current
