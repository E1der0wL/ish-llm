"""Deliver backend Future results and observations on the owning UI loop."""

from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass
from typing import Any

from ..errors.adapters import from_exception
from ..backend.callbacks import CommandCallbacks
from ..errors.contracts import (
    Confidence,
    ErrorCategory,
    ErrorInfo,
    ErrorOrigin,
    HubErrorCode,
    ErrorReason,
)


@dataclass(frozen=True, slots=True)
class RequestResult:
    value: Any = None
    error: ErrorInfo | None = None
    cancelled: bool = False


class UIRequests:
    """Transport only: callers own notices, progress widgets and domain state."""

    ABSENT = None
    REQUIRE_CONNECTION = False
    ALLOW_DISCONNECTED = True
    CANCELLED = True

    def __init__(
        self,
        worker: Callable,
        dispatch: Callable,
        *,
        active: Callable[[], bool],
        connected: Callable[[], bool],
        unavailable: Callable[[], str]
    ):
        self.worker, self.dispatch = worker, dispatch
        self.active, self.connected, self.unavailable = active, connected, unavailable

    def _deliver(self, callback: Callable, *args) -> None:
        if self.active():
            callback(*args)

    def call(
        self,
        operation: str,
        *args,
        completed: Callable[[RequestResult], None],
        acknowledged: Callable | None = ABSENT,
        progress: Callable | None = ABSENT,
        stage: Callable | None = ABSENT,
        allow_disconnected: bool = REQUIRE_CONNECTION
    ) -> None:
        if not self.active():
            return
        worker = self.worker()
        if worker is UIRequests.ABSENT or (
            not self.connected() and not allow_disconnected
        ):
            self._deliver(
                completed,
                RequestResult(
                    error=ErrorInfo(
                        code=HubErrorCode.DISCONNECTED.value,
                        message=self.unavailable(),
                        origin=ErrorOrigin.HUB,
                        operation=operation,
                        category=ErrorCategory.SERVICE,
                        reason=ErrorReason.DISCONNECTED.value,
                        confidence=Confidence.EXPLICIT,
                    )
                ),
            )
            return
        kwargs = CommandCallbacks(
            (
                (lambda: self.dispatch(self._deliver, acknowledged))
                if acknowledged is not UIRequests.ABSENT
                else UIRequests.ABSENT
            ),
            (
                (lambda *values: self.dispatch(self._deliver, progress, *values))
                if progress is not UIRequests.ABSENT
                else UIRequests.ABSENT
            ),
            (
                (lambda update: self.dispatch(self._deliver, stage, update))
                if stage is not UIRequests.ABSENT
                else UIRequests.ABSENT
            ),
        ).to_kwargs()
        try:
            future = worker.call(operation, *args, **kwargs)
        except CancelledError:
            self._deliver(completed, RequestResult(cancelled=UIRequests.CANCELLED))
            return
        except Exception as error:
            self._deliver(
                completed,
                RequestResult(
                    error=from_exception(
                        error, origin=ErrorOrigin.BACKEND, operation=operation
                    )
                ),
            )
            return

        def finished(future):
            try:
                result = RequestResult(value=future.result())
            except CancelledError:
                result = RequestResult(cancelled=UIRequests.CANCELLED)
            except Exception as error:
                result = RequestResult(
                    error=from_exception(
                        error, origin=ErrorOrigin.BACKEND, operation=operation
                    )
                )
            self.dispatch(self._deliver, completed, result)

        future.add_done_callback(finished)
