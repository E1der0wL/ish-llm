"""Opt-in, bounded observation export; never an execution/persistence authority."""

import asyncio
import inspect
from collections import Counter
from copy import deepcopy
from enum import StrEnum
from queue import Empty, Full, Queue
from threading import Lock, Thread, current_thread

from .storage import drain_on_cancel


class ExportOverflow(StrEnum):
    DROP_NEWEST = "drop_newest"
    DROP_OLDEST = "drop_oldest"


class ExportCount(StrEnum):
    ACCEPTED = "accepted"
    EXPORTED = "exported"
    FAILED = "failed"
    DROPPED = "dropped"
    REJECTED = "rejected"


class QueuedSink:
    """Application-owned lifecycle and explicit capacity/overflow policy.

    The worker invokes a synchronous exporter. That exporter owns I/O deadlines;
    Python threads cannot safely terminate arbitrary blocking third-party code.
    close() drains accepted work. aclose() waits off-loop, even during cancellation.
    """

    def __init__(self, sink, *, capacity: int, overflow: ExportOverflow | str):
        if not callable(sink):
            raise TypeError("Observation exporter must be callable")
        if inspect.iscoroutinefunction(sink) or inspect.iscoroutinefunction(getattr(sink, "__call__", None)):
            raise TypeError("Observation exporter must be synchronous")
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("Observation export capacity must be a positive integer")
        self._overflow = ExportOverflow(overflow)
        self._sink = sink
        self._queue = Queue(maxsize=capacity)
        self._lock = Lock()
        self._counts = Counter()
        self._closed = False
        self._stop = object()
        self._worker = Thread(target=self._export, name="llm-observation-export", daemon=True)
        self._worker.start()

    def _export(self):
        while True:
            event = self._queue.get()
            try:
                if event is self._stop:
                    return
                try:
                    result = self._sink(event)
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise TypeError("Observation exporter returned an awaitable")
                except BaseException:
                    status = ExportCount.FAILED
                else:
                    status = ExportCount.EXPORTED
                with self._lock:
                    self._counts[status] += 1
            finally:
                self._queue.task_done()

    def __call__(self, event):
        value = deepcopy(event)
        with self._lock:
            if self._closed:
                self._counts[ExportCount.REJECTED] += 1
                return
            try:
                self._queue.put_nowait(value)
            except Full:
                if self._overflow == ExportOverflow.DROP_NEWEST:
                    self._counts[ExportCount.DROPPED] += 1
                    return
                try:
                    self._queue.get_nowait()
                except Empty:
                    pass  # The worker took it after put_nowait observed Full.
                else:
                    self._queue.task_done()
                    self._counts[ExportCount.DROPPED] += 1
                self._queue.put_nowait(value)
            self._counts[ExportCount.ACCEPTED] += 1

    def snapshot(self):
        """Only cumulative exporter facts; no duplicate Run or queue lifecycle."""
        with self._lock:
            return {name.value: self._counts[name] for name in ExportCount}

    def close(self):
        if current_thread() is self._worker:
            raise RuntimeError("Observation exporter cannot close its own worker")
        with self._lock:
            first = not self._closed
            self._closed = True
        if first:
            self._queue.put(self._stop)
        self._worker.join()

    async def aclose(self):
        await drain_on_cancel(asyncio.to_thread(self.close))

    async def __aenter__(self):
        if self._closed:
            raise RuntimeError("Observation exporter is closed")
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
