"""workspace 단위 프로세스 잠금과 스레드 간 트랜잭션 직렬화를 제공한다. 잠금 파일은 제거하지 않으며 도메인 작업에 백엔드 로그 문맥을 적용한다.

Cooperating services use one OS-backed owner per local workspace.

The lock file is permanent: unlinking it could create two independent locks.
OS handle lifetime, rather than a PID or timeout, determines ownership.
"""

import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from llm._platform import require_linux
from llm.services.infrastructure.logging import logging_scope
from llm.services.infrastructure.transactions import TransactionManager, watch


class WorkspaceBusyError(RuntimeError):
    """Another repository instance/process currently owns this workspace."""


# 프로세스 소유권을 OS 잠금으로 유지하고 동기 트랜잭션을 직렬화한다.
class WorkspaceOwnership:
    def __init__(self, root: Path, *, logger=None) -> None:
        require_linux()
        self.logger = logger
        self.path = root.absolute() / ".ish.lock"
        self.transactions = TransactionManager(self.path.parent)
        self._mutex = threading.RLock()
        self._stream = None
        self._references = 0
        self._pid = os.getpid()
        self._sessions = set()

    def claim_session(self, key) -> None:
        with self._mutex:
            if key in self._sessions:
                raise ValueError("Session already has an attached runtime")
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
                raise RuntimeError("Create a new ProjectRepository after fork")
            if not self._references:
                for candidate in (self.path, *self.path.parents):
                    if candidate.is_symlink():
                        raise ValueError("Linked workspace lock path")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                stream = self.path.open("a+b")
                try:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    stream.close()
                    raise WorkspaceBusyError("Workspace is owned by another service instance") from error
                except BaseException:
                    stream.close()
                    raise
                self._stream = stream
            self._references += 1

    def release(self) -> None:
        with self._mutex:
            if not self._references:
                raise RuntimeError("Workspace ownership is not held")
            self._references -= 1
            if not self._references:
                self._stream.close()
                self._stream = None

    @contextmanager
    def scope(self):
        # Serializes synchronous transactions across threads sharing this owner.
        with self._mutex:
            self.retain()
            try:
                with logging_scope(self.logger), self.transactions.scope():
                    yield
            finally:
                self.release()


def workspace_locked(method):
    """Hold ownership throughout a synchronous service transaction."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.ownership.scope():
            for value in (*args, *kwargs.values()):
                watch(value)
            return method(self, *args, **kwargs)
    return guarded
