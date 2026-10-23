"""Linux 프로세스 그룹의 수명 종료를 공통으로 처리한다."""

import asyncio
import os
import signal
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from .process_state import ProcessStat


_cancellation = ContextVar("llm_process_cancellation", default=None)


class ProcessGroupExit:
    POLL_SECONDS = 0.02
    NO_DEADLINE = None
    ABSENT = False
    DEADLINE_EXCEEDED = "Process group did not terminate within cleanup deadline"

    @classmethod
    def members(cls, group):
        members = {}
        for path in ProcessStat.ROOT.iterdir():
            if not path.name.isdecimal():
                continue
            pid = int(path.name)
            try:
                # Do not inspect private /proc data of unrelated process groups.
                if os.getpgid(pid) != group:
                    continue
                value = ProcessStat.read(pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            if value.process_group == group:
                members[value.identity] = value
        return members

    @classmethod
    def active(cls, observed):
        try:
            current = ProcessStat.read(observed.pid)
        except (FileNotFoundError, ProcessLookupError):
            return cls.ABSENT
        # A recycled PID or a process that left the group is not this owner.
        return (current.identity == observed.identity and current.process_group == observed.process_group
                and not current.terminated)

    @classmethod
    async def wait(cls, process, observed):
        await process.wait()
        while any(cls.active(member) for member in observed.values()):
            await asyncio.sleep(cls.POLL_SECONDS)


class ProcessCancellation:
    """스토리지 스레드의 child만 취소한다. 파일 트랜잭션 자체는 계속 drain한다."""

    ACTIVE = False
    CANCELLED = True

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = self.ACTIVE
        self._callbacks = set()

    @contextmanager
    def scope(self):
        token = _cancellation.set(self)
        try:
            yield
        finally:
            _cancellation.reset(token)

    @contextmanager
    def register(self, callback):
        with self._lock:
            self._callbacks.add(callback)
            cancelled = self._cancelled
        if cancelled:
            callback()
        try:
            yield
        finally:
            with self._lock:
                self._callbacks.discard(callback)

    def cancel(self):
        with self._lock:
            self._cancelled = self.CANCELLED
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            callback()


def current_process_cancellation():
    return _cancellation.get()


async def kill_process_tree(process, *, timeout_seconds=ProcessGroupExit.NO_DEADLINE):
    """새 세션으로 시작한 그룹을 종료한다. setsid로 이탈한 자식은 sandbox가 필요하다."""
    try:
        observed = ProcessGroupExit.members(process.pid)
    finally:
        # An observation failure must not prevent the actual termination signal.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # Include members created between the initial observation and group signal.
    observed.update(ProcessGroupExit.members(process.pid))
    try:
        await asyncio.wait_for(ProcessGroupExit.wait(process, observed), timeout_seconds)
    except asyncio.TimeoutError:
        raise RuntimeError(ProcessGroupExit.DEADLINE_EXCEEDED) from None
