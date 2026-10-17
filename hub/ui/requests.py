"""Deliver backend Future results and observations on the owning UI loop."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RequestResult:
    value: Any = None
    error: Exception | str | None = None


class UIRequests:
    """Transport only: callers own notices, progress widgets and domain state."""

    def __init__(self, worker: Callable, dispatch: Callable, *, active: Callable[[], bool],
                 connected: Callable[[], bool], unavailable: Callable[[], str]):
        self.worker, self.dispatch = worker, dispatch
        self.active, self.connected, self.unavailable = active, connected, unavailable

    def _deliver(self, callback: Callable, *args) -> None:
        if self.active():
            callback(*args)

    def call(self, operation: str, *args, completed: Callable[[RequestResult], None],
             acknowledged: Callable | None = None, progress: Callable | None = None,
             allow_disconnected: bool = False) -> None:
        if not self.active():
            return
        worker = self.worker()
        if worker is None or (not self.connected() and not allow_disconnected):
            self._deliver(completed, RequestResult(error=self.unavailable()))
            return
        kwargs = {}
        if acknowledged is not None:
            kwargs["acknowledged"] = lambda: self.dispatch(self._deliver, acknowledged)
        if progress is not None:
            kwargs["progress"] = lambda *values: self.dispatch(self._deliver, progress, *values)
        try:
            future = worker.call(operation, *args, **kwargs)
        except Exception as error:
            self._deliver(completed, RequestResult(error=error))
            return

        def finished(future):
            try:
                result = RequestResult(value=future.result())
            except Exception as error:
                result = RequestResult(error=error)
            self.dispatch(self._deliver, completed, result)

        future.add_done_callback(finished)
