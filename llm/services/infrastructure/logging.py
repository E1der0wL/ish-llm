"""도메인별 운영 로그를 추가 기록한다. 보관·회전 정책은 Application sink가 소유한다.

Domain-scoped operational logs using Python's logging library."""
from typing import Optional

import json
import logging
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LogConfig:
    """기록 활성화와 조회 이벤트 기록 여부를 설정한다. 보존 정책은 포함하지 않는다."""
    log_reads: Optional[bool] = None
    enabled: Optional[bool] = None

    def __post_init__(self):
        if any(value is not None and type(value) is not bool for value in (self.log_reads, self.enabled)):
            raise ValueError("Log switches must be boolean")


class DomainLogger:
    """sink(logs_path, event_name, fields)를 주입하면 호스트의 로거로 연결할 수 있다."""
    def __init__(self, config: Optional[LogConfig] = None, *, sink: Optional[Callable] = None):
        self.config = config if config is not None else LogConfig()
        if sink is not None and not callable(sink):
            raise TypeError("Log sink must be callable")
        self.sink = sink

    def write(self, logs: Path, event: str, **fields):
        if self.config.enabled is False or (event.endswith(".loaded") and self.config.log_reads is False):
            return
        if self.sink is None:
            FileLogSink()(logs, event, fields)
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


class FileLogSink:
    """Allowlisted file writer; an Application may override open_handler to own retention."""

    STATUSES = frozenset(("idle", "running", "deleted", "queued", "committed", "streaming",
                          "pending", "completed", "interrupted", "cancelled", "failed", "paused"))


    def open_handler(self, path: Path):
        return _RaisingFileHandler(path, encoding="utf-8")

    def __call__(self, logs: Path, event: str, fields: dict):
        _file_log_event(logs, event, handler_factory=self.open_handler, **fields)

    @staticmethod
    def handle_error(record: logging.LogRecord) -> None:
        # The caller emits a safe warning without the record or exception text.
        raise OSError("Operational log write failed")


class _RaisingFileHandler(logging.FileHandler):
    handleError = staticmethod(FileLogSink.handle_error)


def _file_log_event(logs: Path, event: str, *, handler_factory: Callable, entity_id: Optional[str] = None,
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
    if status in FileLogSink.STATUSES:
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
        if path.is_symlink():
            raise OSError("Linked log file")
        handler = handler_factory(path)
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
