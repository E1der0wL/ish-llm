"""Application-owned operational log rotation; no implicit retention policy."""

from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading

from llm.services.infrastructure.logging import FileLogSink


class _RaisingRotatingHandler(RotatingFileHandler):
    handleError = staticmethod(FileLogSink.handle_error)


@dataclass(frozen=True, slots=True)
class RotatingLogSink(FileLogSink):
    max_bytes: int
    backup_count: int
    _lock: object = field(default_factory=threading.RLock, init=False, repr=False, compare=False)

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in (self.max_bytes, self.backup_count)):
            raise ValueError("Log rotation requires positive integer size and backup count")

    def open_handler(self, path: Path):
        for index in range(1, self.backup_count + 1):
            if path.with_name(f"{path.name}.{index}").is_symlink():
                raise OSError("Linked log backup")
        return _RaisingRotatingHandler(path, maxBytes=self.max_bytes,
                                      backupCount=self.backup_count, encoding="utf-8")

    def __call__(self, logs: Path, event: str, fields: dict):
        with self._lock:
            FileLogSink.__call__(self, logs, event, fields)
