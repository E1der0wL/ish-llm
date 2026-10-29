"""Task-local, observation-only stages for foreground backend operations."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar

from ..locales.keys import MessageKey
from .project.management_values import ManagementAction
from .project.settings_contracts import SettingsOperation
from ..model import RAGAction


@dataclass(frozen=True, slots=True)
class BackendProgress:
    INITIAL_COUNT: ClassVar[int] = 0
    key: str
    completed: int = INITIAL_COUNT
    total: int | None = None


class OperationProgress:
    ACTIVE = True
    CLOSED = False
    MANAGEMENT_WRITES = frozenset({
        ManagementAction.IMPORT, ManagementAction.CREATE, ManagementAction.SAVE,
        ManagementAction.DELETE, ManagementAction.TOGGLE,
        RAGAction.RETRY, RAGAction.DELETE_JOB, RAGAction.CANCEL_JOB,
    })
    MANAGEMENT_READS = frozenset({
        ManagementAction.LIST, ManagementAction.READ, ManagementAction.OPEN,
        RAGAction.JOBS, RAGAction.JOB_DELETE_PLAN, RAGAction.VISION_SETTINGS,
    })
    SETTINGS_WRITES = frozenset({
        SettingsOperation.SAVE_PROJECT, SettingsOperation.CREATE_PROJECT,
        SettingsOperation.SELECT_COMPONENTS, SettingsOperation.CLONE_PROJECT,
        SettingsOperation.DELETE_PROJECT, SettingsOperation.RENAME_PROJECT,
        SettingsOperation.SAVE_GLOBAL, SettingsOperation.SAVE_ALL,
        SettingsOperation.PRESET_SAVE, SettingsOperation.PRESET_APPLY,
    })
    SETTINGS_READS = frozenset({
        SettingsOperation.CATALOG, SettingsOperation.LIST_PROJECTS, SettingsOperation.USAGE,
        SettingsOperation.LOAD_PROJECT, SettingsOperation.ACTIVATE_PROJECT,
        SettingsOperation.PRESET_LIST, SettingsOperation.PRESET_EXPORT,
    })
    _observer: ContextVar[Callable[[BackendProgress], None] | None] = ContextVar(
        "hub_operation_progress", default=None
    )

    @classmethod
    @contextmanager
    def bind(cls, observer: Callable[[BackendProgress], None] | None) -> Iterator[None]:
        active = cls.ACTIVE

        def deliver(update: BackendProgress) -> None:
            if active and observer is not None:
                observer(update)
        token = cls._observer.set(deliver)
        try:
            yield
        finally:
            active = cls.CLOSED
            cls._observer.reset(token)

    @classmethod
    def report(cls, key: str, completed: int = BackendProgress.INITIAL_COUNT, total: int | None = None) -> None:
        observer = cls._observer.get()
        if observer is not None:
            observer(BackendProgress(key, completed, total))

    @classmethod
    def management(cls, action) -> MessageKey:
        if action in cls.MANAGEMENT_WRITES:
            return MessageKey.PROGRESS_BACKEND_WRITE
        if action in cls.MANAGEMENT_READS:
            return MessageKey.PROGRESS_BACKEND_READ
        return MessageKey.PROGRESS_BACKEND_WORK

    @classmethod
    def settings(cls, operation) -> MessageKey:
        if operation in cls.SETTINGS_WRITES:
            return MessageKey.PROGRESS_BACKEND_WRITE
        if operation in cls.SETTINGS_READS:
            return MessageKey.PROGRESS_BACKEND_READ
        return MessageKey.PROGRESS_BACKEND_WORK
