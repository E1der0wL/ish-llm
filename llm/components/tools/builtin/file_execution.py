"""Cooperative file preparation and indivisible publication, owned by file Tools.

This does not cancel StorageIO transactions, change approval or promise rollback.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import threading


_current = ContextVar('llm_file_execution', default=None)


class FileExecution:
    # Responsiveness of an internal lock wait; never ends or rejects a request.
    LOCK_POLL_SECONDS = 0.05

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._committing = False

    def cancel(self):
        with self._lock:
            self._cancelled = True

    def check(self, *, commit=False):
        with self._lock:
            if self._cancelled and not self._committing:
                raise asyncio.CancelledError
            if commit:
                # Linearization point: cancellation before this wins; afterwards
                # publication, durability and the actual result must finish.
                self._committing = True

    def run(self, function, args):
        token = _current.set(self)
        try:
            self.check()
            return function(*args)
        finally:
            _current.reset(token)


def check_cancelled():
    execution = _current.get()
    if execution is not None:
        execution.check()


def begin_commit():
    execution = _current.get()
    if execution is not None:
        execution.check(commit=True)


async def execute_file(function, *args):
    execution = FileExecution()
    pending = asyncio.create_task(asyncio.to_thread(execution.run, function, args))
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            # Never abandon a thread with file descriptors or a temporary file.
            # It cooperates between blocks, or finishes an already entered commit.
            execution.cancel()
    # A completed result wins the invocation redirect race. Ordinary Run/Tool
    # cancellation still wins at the enclosing execution boundary.
    return pending.result()


@contextmanager
def file_lock(lock):
    # Polling is only to observe cancellation, not a lock-wait deadline/limit.
    check_cancelled()
    while not lock.acquire(timeout=FileExecution.LOCK_POLL_SECONDS):
        check_cancelled()
    try:
        check_cancelled()
        yield
    finally:
        lock.release()


class FileReader:
    """Check each bounded read without injecting Tool state into generic text I/O."""

    def __init__(self, stream):
        self.stream = stream

    def read(self, size):
        check_cancelled()
        value = self.stream.read(size)
        check_cancelled()
        return value

    def seek(self, *args):
        check_cancelled()
        return self.stream.seek(*args)
