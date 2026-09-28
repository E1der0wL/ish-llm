"""도메인별 운영 로그를 기록한다. 기본값은 회전 파일 로그이며 LogSettings와 DomainLogger sink로 호스트 출력에 연결할 수 있다.

Domain-scoped, rotating operational logs using Python's logging library."""
from typing import Optional

import json
import logging
import re
import warnings
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable
from llm.compat import dataclass


@dataclass(frozen=True, slots=True)
class LogSettings:
    """기본 파일 로그의 크기/보관 개수와 조회 로그 여부를 설정한다."""
    max_bytes: int = 1_048_576
    backup_count: int = 3
    log_reads: bool = True
    enabled: bool = True

    def __post_init__(self):
        if type(self.max_bytes) is not int or self.max_bytes < 1:
            raise ValueError("Log max_bytes must be positive")
        if type(self.backup_count) is not int or self.backup_count < 1:
            raise ValueError("Log backup_count must be positive")
        if type(self.log_reads) is not bool or type(self.enabled) is not bool:
            raise ValueError("Log switches must be boolean")


class DomainLogger:
    """sink(logs_path, event_name, fields)를 주입하면 호스트의 로거로 연결할 수 있다."""
    def __init__(self, settings: Optional[LogSettings] = None, *, sink: Optional[Callable] = None):
        self.settings = settings if settings is not None else LogSettings()
        if sink is not None and not callable(sink):
            raise TypeError("Log sink must be callable")
        self.sink = sink

    def write(self, logs: Path, event: str, **fields):
        if not self.settings.enabled or (event.endswith(".loaded") and not self.settings.log_reads):
            return
        if self.sink is None:
            _file_log_event(logs, event, settings=self.settings, **fields)
        else:
            try:
                self.sink(logs, event, fields)
            except Exception:
                warnings.warn("llm log sink failed", RuntimeWarning, stacklevel=2)


_logger_context = ContextVar("llm_domain_logger", default=None)


@contextmanager
def logging_scope(logger):
    """백엔드마다 로그 설정을 격리한다. 중첩된 저장 트랜잭션에서도 복원한다."""
    token = _logger_context.set(logger)
    try:
        yield
    finally:
        _logger_context.reset(token)


def log_event(logs: Path, event: str, **fields) -> None:
    from llm.services.infrastructure.transactions import after_commit
    logger = _logger_context.get()
    after_commit(lambda: (logger if logger is not None else DomainLogger()).write(logs, event, **fields))


class _RaisingFileHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # The caller emits a safe warning without the record or exception text.
        raise OSError("Operational log write failed")


def _file_log_event(logs: Path, event: str, *, settings: LogSettings, entity_id: Optional[str] = None,
              related_id: Optional[str] = None, status: Optional[str] = None,
              count: Optional[int] = None, permanent: Optional[bool] = None) -> None:
    """Write only allowlisted operational fields; never accept arbitrary metadata.

    No global logger/handler cache and no open handle survives this call. This
    bounds descriptor lifetime and avoids duplicate handlers. Logs are best
    effort, not a durable audit transaction. The domain root must already exist.
    """
    if not re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)+", event):
        raise ValueError("Invalid operational event name")
    if not logs.parent.is_dir():
        return
    payload: dict = {"time": datetime.now(timezone.utc).isoformat(), "event": event}
    for key, identifier in (("entity_id", entity_id), ("related_id", related_id)):
        if identifier is not None and re.fullmatch(r"[a-f0-9]{32}", identifier):
            payload[key] = identifier
    if status in {"idle", "running", "deleted", "queued", "committed", "streaming",
                  "pending", "completed", "interrupted", "cancelled", "failed", "paused"}:
        payload["status"] = status
    if type(count) is int and count >= 0:
        payload["count"] = count
    if type(permanent) is bool:
        payload["permanent"] = permanent
    handler = None
    logger = logging.Logger("llm.service", level=logging.INFO)
    logger.propagate = False
    try:
        for directory in (logs, *logs.parents):
            if directory.is_symlink():
                raise OSError("Linked log directory")
        logs.mkdir(mode=0o700, exist_ok=True)
        path = logs / "service.log"
        for candidate in (path, *(logs / f"service.log.{i}" for i in range(1, settings.backup_count + 1))):
            if candidate.is_symlink():
                raise OSError("Linked log file")
        handler = _RaisingFileHandler(path, maxBytes=settings.max_bytes, backupCount=settings.backup_count, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        level = logging.ERROR if event.endswith(".failed") else logging.INFO
        logger.log(level, json.dumps(payload, ensure_ascii=False))
    except OSError:
        warnings.warn("llm could not write an operational log", RuntimeWarning, stacklevel=2)
    finally:
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
