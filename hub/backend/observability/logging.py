"""Application-owned operational log rotation; no implicit retention policy."""

from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading

from llm.services.infrastructure.logging import FileLogSink, LoggingFormat
from typing import ClassVar


class _RaisingRotatingHandler(RotatingFileHandler):
    handleError = staticmethod(FileLogSink.handle_error)


@dataclass(frozen=True, slots=True)
class RotatingLogSink(FileLogSink):
    MINIMUM_LIMIT: ClassVar[int] = 1
    FIRST_BACKUP: ClassVar[int] = 1
    INVALID_LIMIT: ClassVar[str] = (
        "Log rotation requires positive integer size and backup count"
    )
    LINKED_BACKUP: ClassVar[str] = "Linked log backup"
    BACKUP_NAME: ClassVar[str] = "{name}.{index}"

    max_bytes: int
    backup_count: int
    _lock: object = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )

    def __post_init__(self):
        if any(
            type(value) is not int or value < self.MINIMUM_LIMIT
            for value in (self.max_bytes, self.backup_count)
        ):
            raise ValueError(self.INVALID_LIMIT)

    def open_handler(self, path: Path):
        for index in range(self.FIRST_BACKUP, self.backup_count + self.FIRST_BACKUP):
            if path.with_name(
                self.BACKUP_NAME.format(name=path.name, index=index)
            ).is_symlink():
                raise OSError(self.LINKED_BACKUP)
        return _RaisingRotatingHandler(
            path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
            encoding=LoggingFormat.ENCODING,
        )

    def __call__(self, logs: Path, event: str, fields: dict):
        with self._lock:
            FileLogSink.__call__(self, logs, event, fields)
