"""workspace 단위 프로세스 잠금과 스레드 간 트랜잭션 직렬화를 제공한다. 잠금 파일은 제거하지 않으며 도메인 작업에 백엔드 로그 문맥을 적용한다.

Cooperating services use one OS-backed owner per local workspace.

The lock file is permanent: unlinking it could create two independent locks.
OS handle lifetime, rather than a PID or timeout, determines ownership.
"""

import os
import threading
from contextlib import contextmanager, nullcontext
from functools import wraps
from pathlib import Path

from llm._platform import require_linux
from llm.core.paths import WorkspacePaths
from llm.services.infrastructure.logging import logging_scope
from llm.services.infrastructure.observability import ObservationRuntime
from llm.services.infrastructure.transactions import TransactionManager, watch


class WorkspaceLock:
    ABSENT = None
    FILE = WorkspacePaths.LOCK
    ZERO_REFERENCES = 0
    REFERENCE = 1
    ATTACHED = "Session already has an attached runtime"
    FORKED = "Create a new ProjectRepository after fork"
    LINKED = "Linked workspace lock path"
    CREATE_PARENTS = True
    OPEN_MODE = "a+b"
    BUSY = "Workspace is owned by another service instance"
    NOT_HELD = "Workspace ownership is not held"


class WorkspaceBusyError(RuntimeError):
    """Another repository instance/process currently owns this workspace."""


# 프로세스 소유권을 OS 잠금으로 유지하고 동기 트랜잭션을 직렬화한다.
class WorkspaceOwnership:
    def __init__(self, root: Path, *, logger=WorkspaceLock.ABSENT) -> None:
        require_linux()
        self.logger = logger
        self.path = root.absolute() / WorkspaceLock.FILE
        self.transactions = TransactionManager(self.path.parent)
        self._mutex = threading.RLock()
        self._stream = WorkspaceLock.ABSENT
        self._references = WorkspaceLock.ZERO_REFERENCES
        self._pid = os.getpid()
        self._sessions = set()

    def claim_session(self, key) -> None:
        with self._mutex:
            if key in self._sessions:
                raise ValueError(WorkspaceLock.ATTACHED)
            self.retain()
            self._sessions.add(key)

    def release_session(self, key) -> None:
        with self._mutex:
            self._sessions.remove(key)
            self.release()

    def session_attached(self, key) -> bool:
        with self._mutex:
            return key in self._sessions

    @property
    def attached_sessions(self):
        """유지 중인 런타임 소유권의 독립 스냅샷."""
        with self._mutex:
            return tuple(self._sessions)

    def retain(self) -> None:
        with self._mutex:
            if os.getpid() != self._pid:
                raise RuntimeError(WorkspaceLock.FORKED)
            if not self._references:
                for candidate in (self.path, *self.path.parents):
                    if candidate.is_symlink():
                        raise ValueError(WorkspaceLock.LINKED)
                self.path.parent.mkdir(
                    parents=WorkspaceLock.CREATE_PARENTS, exist_ok=WorkspaceLock.CREATE_PARENTS
                )
                stream = self.path.open(WorkspaceLock.OPEN_MODE)
                try:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    stream.close()
                    raise WorkspaceBusyError(WorkspaceLock.BUSY) from error
                except BaseException:
                    stream.close()
                    raise
                self._stream = stream
                self.transactions.records.reset()
            self._references += WorkspaceLock.REFERENCE

    def release(self) -> None:
        with self._mutex:
            if not self._references:
                raise RuntimeError(WorkspaceLock.NOT_HELD)
            self._references -= WorkspaceLock.REFERENCE
            if not self._references:
                try:
                    self.transactions.records.checkpoint()
                finally:
                    self.transactions.records.reset()
                    self._stream.close()
                    self._stream = WorkspaceLock.ABSENT

    @contextmanager
    def scope(self, *, records=False):
        # Serializes synchronous transactions across threads sharing this owner.
        with self._mutex:
            self.retain()
            try:
                with logging_scope(self.logger), self.transactions.scope(records=records):
                    yield
            finally:
                self.release()


def workspace_locked(method=None, *, records=False):
    """Hold ownership throughout a synchronous service transaction."""

    if method is None:
        return lambda operation: workspace_locked(operation, records=records)

    @wraps(method)
    def guarded(self, *args, **kwargs):
        # Facade에 바인딩된 동기 Component API도 같은 Host 관찰 문맥을 전달한다.
        # 여기서 계측하거나 상태를 집계하지는 않는다.
        observer = getattr(self, ObservationRuntime.BINDING, WorkspaceLock.ABSENT)
        with (
            observer.scope() if observer is not WorkspaceLock.ABSENT else nullcontext(),
            self.ownership.scope(records=records),
        ):
            for value in (*args, *kwargs.values()):
                watch(value)
            return method(self, *args, **kwargs)

    # StorageIO enters the outer scope before invoking this decorated method.
    # Only an explicit operation declaration selects the restricted record API.
    guarded.__record_batch__ = records
    return guarded
