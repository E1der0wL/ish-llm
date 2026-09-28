"""Task의 영속 상태와 수명 주기를 관리한다. TaskRuntime은 실행 전용이며 RunManager가 소유한다. 제목/메타데이터 수정은 실행 설정 변경과 분리한다."""

from llm.services.infrastructure.storage import read_domain_record, atomic_domain_json
from llm.services.infrastructure.storage import make_directory
from llm.services.infrastructure.transactions import after_commit, current_transaction

from llm.services.query import queryable
from typing import Optional, Union
import asyncio
import threading
from dataclasses import field
from llm.compat import dataclass
from copy import deepcopy
from collections.abc import Callable

from llm.core.models import Project, ProjectConfig, Task, TaskStatus, new_id
from llm.core.paths import ProjectPaths, TaskPaths
from llm.services.history.conversation import Conversation, ProjectConversations, conversation_store
from llm.services.history.context import ConversationContextBuilder
from llm.services.lifecycle.access import ProjectAccess
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import atomic_json, child, read_json, record, remove_owned_tree


@dataclass(slots=True)
class TaskRuntime:
    """Runtime-only state, owned and scheduled exclusively by RunManager."""

    project: Project
    task: Task
    queue: asyncio.Queue[Optional[str]] = field(default_factory=asyncio.Queue)
    worker: Optional[asyncio.Task[None]] = None
    execution: Optional[asyncio.Task[None]] = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False
    preparing: bool = False
    interrupt_requested: bool = False


# 프로젝트 아래의 Task 메타데이터를 읽고 쓴다.
class TaskRepository:
    """Task metadata and paths beneath the owning Project's tasks root."""

    def _load(self, paths: TaskPaths, task_id: str, project_id: str) -> Task:
        data = read_domain_record(paths.root / "task.json")
        if data["id"] != task_id or data["project_id"] != project_id:
            raise ValueError("Task ownership mismatch")
        ProjectConfig.validate_task(data["config"])
        log_event(paths.logs, "task.loaded", entity_id=task_id)
        return Task(**{**data, "status": TaskStatus(data["status"]), "paths": paths})

    def read_backup(self, project):
        """Task 파일/대화만 검증한다. 실행 복구나 로그 쓰기를 하지 않는다."""
        from llm.services.history.conversation import ConversationStore
        tasks = []
        for folder in project.paths.tasks.iterdir():
            if not folder.is_dir():
                continue
            data = read_domain_record(folder / "task.json")
            if child(project.paths.tasks, data["id"]) != folder or data["project_id"] != project.id:
                raise ValueError("Backup Task ownership mismatch")
            task = Task(**{**data, "paths": TaskPaths(folder), "status": TaskStatus(data["status"])})
            ProjectConfig.validate_task(task.config)
            if task.status == TaskStatus.RUNNING or task.current_run_id is not None:
                raise ValueError("Backup contains an active Task")
            ConversationStore(task.paths.conversation).list()
            tasks.append(task)
        return tasks

    # 공개 API
    def paths(self, project: Project, task_id: str) -> TaskPaths:
        return TaskPaths(child(project.paths.tasks, task_id))

    def initialize(self, project: Project) -> None:
        make_directory(project.paths.tasks)
        log_event(project.paths.logs, "tasks.initialized", entity_id=project.id)

    def save(self, task: Task) -> None:
        ProjectConfig.validate_task(task.config)
        atomic_domain_json(task.paths.root / "task.json", record(task))
        log_event(task.paths.logs, "task.saved", entity_id=task.id, status=task.status)

    def load(self, project: Project, task_id: str) -> Task:
        return self._load(self.paths(project, task_id), task_id, project.id)

    def reload(self, task: Task) -> Task:
        return self._load(task.paths, task.id, task.project_id)

    def delete(self, task: Task) -> None:
        """Remove the Task subtree only after validating layout and ownership."""
        root = task.paths.root
        if (root.name != task.id or root.parent.name != "tasks"
                or root.parent.parent.name != task.project_id):
            raise ValueError("Task path ownership mismatch")
        self.reload(task)
        remove_owned_tree(root.parent, root, task.id)
        log_event(ProjectPaths(root.parent.parent).logs, "task.deleted",
                  entity_id=task.id, related_id=task.project_id, permanent=True)

    @queryable
    def list(self, project: Project, *, include_deleted: bool = False) -> list[Task]:
        tasks = [self.load(project, path.parent.name)
                 for path in project.paths.tasks.glob("*/task.json")]
        return sorted((task for task in tasks
                       if include_deleted or task.status != TaskStatus.DELETED),
                      key=lambda task: (task.created_at, task.id))


# 세션 상태를 관리하며 Engine 실행은 RunManager에 맡긴다.
class TaskManager:
    """Task lifecycle only. Call lifecycle mutations while runtime is detached."""

    def __init__(self, repository: Optional[TaskRepository] = None, *,
                 project_access: Optional[ProjectAccess] = None,
                 conversations: Union[str, Callable[[Task], Conversation]] = conversation_store,
                 context_builder: Optional[ConversationContextBuilder] = None,
                 conversation_cache_size: int = 32,
                 runs=None) -> None:
        self.repository = repository if repository is not None else TaskRepository()
        self._attached: set[tuple[str, str]] = set()
        self._close_when_idle = False
        self._runtime_lock = threading.RLock()
        self.project_access = project_access
        self.conversations = ProjectConversations(self._conversation_storage, default=conversations,
                                                  file_cache_size=conversation_cache_size)
        self.context_builder = context_builder if context_builder is not None else ConversationContextBuilder()
        self.run_repository = runs
        self.configuration_validator = None

    def _conversation_storage(self, task: Task) -> Optional[str]:
        """오래된 핸들의 복사본 대신 소유 프로젝트의 현재 선택을 조회한다."""
        return self._owner(task, allow_deleted=True).conversation_storage

    def _discard_conversation(self, task: Task) -> None:
        """상위 서비스가 영구 삭제를 완료한 뒤 선택적 저장소 정리 계약을 호출한다."""
        discard = getattr(self.conversations, "discard", None)
        if callable(discard):
            after_commit(lambda: discard(task))

    def _owner(self, task: Task, *, allow_deleted: bool = False) -> Project:
        if self.project_access is None:
            raise RuntimeError("TaskManager needs project_access")
        project = self.project_access.load(task.project_id, allow_deleted=allow_deleted)
        expected = self.repository.paths(project, task.id)
        if task.paths.root.absolute() != expected.root.absolute():
            raise ValueError("Task path ownership mismatch")
        return project

    @workspace_locked
    def _save_runtime(self, task: Task) -> None:
        """Internal RunManager state transitions; public save edits configuration only."""
        self._owner(task)
        if (task.project_id, task.id) not in self._attached:
            raise ValueError("Runtime state requires an attached Task")
        current = self.repository.reload(task)
        if current.status == TaskStatus.DELETED:
            raise ValueError("Task is deleted")
        # 오래 유지된 런타임 스냅샷이 UI의 제목/메타데이터 수정을 덮어쓰지 않는다.
        task.title, task.metadata = current.title, deepcopy(current.metadata)
        self.repository.save(task)

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
                raise RuntimeError("Shut down Task runtimes before closing conversation storage")
            self.conversations.clear_owned()

    @workspace_locked
    def update_details(self, task: Task, *, title=None, metadata=None) -> None:
        """실행 중에도 제목/메타데이터만 최신 상태에 합쳐 저장한다. 설정은 변경하지 않는다."""
        self._owner(task)
        current = self.repository.reload(task)
        if current.status == TaskStatus.DELETED:
            raise ValueError("Task is deleted")
        if title is not None:
            if not isinstance(title, str):
                raise TypeError("Task title must be a string")
            current.title = title
        if metadata is not None:
            ProjectConfig.validate_settings(metadata)
            current.metadata = deepcopy(metadata)
        self.repository.save(current)

    @property
    def ownership(self):
        if self.project_access is None:
            raise RuntimeError("TaskManager needs project_access")
        return self.project_access.repository.ownership

    def bind_project_access(self, access: ProjectAccess) -> None:
        if self.project_access is not None and self.project_access.repository is not access.repository:
            raise ValueError("TaskManager is already bound to a Project repository")
        self.project_access = access

    def require_project(self, project: Project, *, allow_deleted: bool = False) -> Project:
        if self.project_access is None:
            raise RuntimeError("Bind TaskManager through ProjectManager or inject project_access")
        return self.project_access.require(project, allow_deleted=allow_deleted)

    @workspace_locked
    def attach_runtime(self, task: Task) -> None:
        self._owner(task)
        if any((task.paths.state / "maintenance").glob("*.json")):
            raise ValueError("Task maintenance is pending; recover retention before starting runtime")
        with self._runtime_lock:
            key = (task.project_id, task.id)
            if key in self._attached:
                raise ValueError("Task already has an attached runtime")
            self.ownership.claim_task(key)
            self._attached.add(key)
            transaction = current_transaction()
            if transaction is not None:
                def undo_attachment():
                    with self._runtime_lock:
                        if key in self._attached:
                            self._attached.remove(key)
                            self.ownership.release_task(key)
                transaction.on_error(("runtime_attachment", id(self), key), undo_attachment)

    @workspace_locked
    def detach_runtime(self, task: Task) -> None:
        with self._runtime_lock:
            key = (task.project_id, task.id)
            if key in self._attached:
                self._attached.remove(key)
                self.ownership.release_task(key)
            if not self._attached and self._close_when_idle:
                self._close_when_idle = False
                self.conversations.clear_owned()

    @workspace_locked
    def initialize(self, project: Project) -> None:
        project = self.require_project(project)
        self.repository.initialize(project)

    @workspace_locked
    def create(self, project: Project, title: str, *,
               config: Optional[dict] = None) -> Task:
        project = self.require_project(project)
        ProjectConfig.validate_task(config if config is not None else {})
        settings = ProjectConfig.merge(project.config.task_defaults, config or {})
        if self.configuration_validator is not None:
            self.configuration_validator(project.config, task_config=settings)
        self.initialize(project)
        task_id = new_id()
        task = Task(task_id, project.id, title, self.repository.paths(project, task_id),
                    config=settings)
        self.repository.save(task)
        log_event(task.paths.logs, "task.created", entity_id=task.id,
                  related_id=project.id)
        return task

    @workspace_locked
    def save(self, task: Task) -> None:
        project = self._owner(task)
        if self.configuration_validator is not None:
            self.configuration_validator(project.config, task_config=task.config)
        current = self.require_inactive(task)
        if current.status == TaskStatus.DELETED:
            raise ValueError("Task is deleted")
        if task.status != current.status or task.current_run_id != current.current_run_id:
            raise ValueError("Task execution state is managed by RunManager")
        current.title = task.title
        current.metadata = deepcopy(task.metadata)
        current.config = deepcopy(task.config)
        self.repository.save(current)

    @workspace_locked
    def load(self, project: Project, task_id: str) -> Task:
        project = self.require_project(project, allow_deleted=True)
        return self.repository.load(project, task_id)

    @workspace_locked
    @queryable
    def list(self, project: Project, *, include_deleted: bool = False) -> list[Task]:
        project = self.require_project(project, allow_deleted=True)
        return self.repository.list(project, include_deleted=include_deleted)

    @workspace_locked
    def delete(self, task: Task, *, permanent: bool = False) -> None:
        """Mark deleted by default; permanent=True removes history and artifacts."""
        if type(permanent) is not bool:
            raise TypeError("permanent must be a bool")
        current = self.require_inactive(task)
        if permanent:
            self.repository.delete(current)
            self._discard_conversation(current)
        else:
            current.status = TaskStatus.DELETED
            self.repository.save(current)
            log_event(current.paths.logs, "task.deleted", entity_id=current.id,
                      permanent=False)
        task.status = TaskStatus.DELETED

    @workspace_locked
    def restore(self, task: Task) -> None:
        self._owner(task)
        current = self.require_inactive(task)
        if current.status != TaskStatus.DELETED:
            raise ValueError("Task is not deleted")
        current.status = TaskStatus.IDLE
        self.repository.save(current)
        task.status = TaskStatus.IDLE
        log_event(current.paths.logs, "task.restored", entity_id=current.id)

    @workspace_locked
    def clone(self, source: Task, project: Project, *, title: Optional[str] = None) -> Task:
        """Copy conversation/configuration, with no execution history or queue replay."""
        self._owner(source)
        source = self.require_inactive(source)
        if source.status == TaskStatus.DELETED:
            raise ValueError("Task is deleted")
        destination_project = self.require_project(project)
        if self.configuration_validator is not None:
            self.configuration_validator(destination_project.config, task_config=source.config)
        clone = self.create(project, title if title is not None else source.title)
        clone.metadata = deepcopy(source.metadata)
        clone.config = deepcopy(source.config)
        destination = self.conversations(clone)
        messages = self.context_builder.for_clone(self.conversations(source).list())
        for message in messages:
            destination.create(message.role, message.content, message.status,
                               metadata=message.metadata)
        self.save(clone)
        log_event(clone.paths.logs, "task.cloned", entity_id=clone.id,
                  related_id=source.id)
        return clone

    @workspace_locked
    def require_inactive(self, task: Task) -> Task:
        self._owner(task, allow_deleted=True)
        if self.ownership.task_attached((task.project_id, task.id)):
            raise ValueError("Task runtime is attached; shut down RunManager first")
        # Reload to avoid trusting a stale handle held by a caller.
        current = self.repository.reload(task)
        if current.status == TaskStatus.RUNNING or current.current_run_id is not None:
            raise ValueError("Task has an active Run")
        return current
