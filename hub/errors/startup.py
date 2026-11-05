"""Startup observations, separate from backend error and execution authority."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adapters import from_exception
from .contracts import ErrorInfo, ErrorOrigin
from ..locales.keys import MessageKey


class StartupLoadError(Exception):
    """A loading owner identifies its target; never inferred from error text."""

    def __init__(self, error, target, *, reset=None, hint=None):
        super().__init__(str(error))
        self.error, self.target = error, str(target)
        self.reset, self.hint = reset, hint


@dataclass(frozen=True, slots=True)
class StartupFailure:
    OPERATION = 'startup'
    error: ErrorInfo
    stage: str
    target: str
    hint: str
    reset: Callable[[], Path] | None = None
    retry_allowed: bool = True

    @classmethod
    def observe(cls, error, stage, target, *, retry_allowed=True):
        reset = None
        hint = MessageKey.STARTUP_BACKEND_HINT
        if isinstance(error, StartupLoadError):
            target, reset = error.target, error.reset
            hint = error.hint or MessageKey.STARTUP_RESET_HINT
            error = error.error
        elif isinstance(error, PermissionError):
            hint = MessageKey.STARTUP_PERMISSION_HINT
        elif isinstance(error, (ImportError, ModuleNotFoundError)):
            hint = MessageKey.STARTUP_INSTALL_HINT
        if not retry_allowed:
            hint, reset = MessageKey.STARTUP_CLEANUP_HINT, None
        return cls(from_exception(error, origin=ErrorOrigin.BACKEND, operation=StartupFailure.OPERATION),
                   stage, str(target), hint, reset, retry_allowed)
