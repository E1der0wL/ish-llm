"""도메인별 운영 로그를 추가 기록한다. 보관·회전 정책은 Application sink가 소유한다.

Domain-scoped operational logs using Python's logging library."""

from llm.core.models import SessionStatus, MessageStatus, RunStatus
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


class LoggingFormat:
    ABSENT = None
    ENSURE_ASCII = False
    PROPAGATE = False
    CREATE_DIRECTORY = True
    FAILED_SUFFIX = ".failed"
    WARNING_DEPTH = 2
    CONTEXT_NAME = "llm_domain_logger"
    ENCODING = "utf-8"
    WRITE_FAILED = "Operational log write failed"
    EVENT_PATTERN = r"[a-z_]+(?:\.[a-z_]+)+"
    INVALID_EVENT = "Invalid operational event name"
    LOGGER_NAME = "llm.service"
    LINKED_DIRECTORY = "Linked log directory"
    DIRECTORY_MODE = 0o700
    FILE = "service.log"
    LINKED_FILE = "Linked log file"
    MESSAGE_FORMAT = "%(message)s"
    WRITE_WARNING = "llm could not write an operational log"


@dataclass(frozen=True, slots=True)
class LogConfig:
    """기록 활성화와 조회 이벤트 기록 여부를 설정한다. 보존 정책은 포함하지 않는다."""

    DISABLED = False

    log_reads: Optional[bool] = LoggingFormat.ABSENT
    enabled: Optional[bool] = LoggingFormat.ABSENT

    INVALID_SWITCH = "Log switches must be boolean"

    def __post_init__(self):
        if any(
            value is not LoggingFormat.ABSENT and type(value) is not bool
            for value in (self.log_reads, self.enabled)
        ):
            raise ValueError(LogConfig.INVALID_SWITCH)


class DomainLogger:
    """sink(logs_path, event_name, fields)를 주입하면 호스트의 로거로 연결할 수 있다."""

    INVALID_SINK = "Log sink must be callable"

    READ_SUFFIX = ".loaded"

    SINK_WARNING = "llm log sink failed"

    def __init__(
        self,
        config: Optional[LogConfig] = LoggingFormat.ABSENT,
        *,
        sink: Optional[Callable] = LoggingFormat.ABSENT,
    ):
        self.config = config if config is not LoggingFormat.ABSENT else LogConfig()
        if sink is not LoggingFormat.ABSENT and not callable(sink):
            raise TypeError(DomainLogger.INVALID_SINK)
        self.sink = sink

    def write(self, logs: Path, event: str, **fields):
        if self.config.enabled is LogConfig.DISABLED or (
            event.endswith(DomainLogger.READ_SUFFIX) and self.config.log_reads is LogConfig.DISABLED
        ):
            return
        if self.sink is LoggingFormat.ABSENT:
            FileLogSink()(logs, event, fields)
        else:
            try:
                self.sink(logs, event, fields)
            except Exception:
                warnings.warn(
                    DomainLogger.SINK_WARNING, RuntimeWarning, stacklevel=LoggingFormat.WARNING_DEPTH
                )


_logger_context = ContextVar(LoggingFormat.CONTEXT_NAME, default=LoggingFormat.ABSENT)


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
    after_commit(
        lambda: (logger if logger is not LoggingFormat.ABSENT else DomainLogger()).write(
            logs, event, **fields
        )
    )


class OperationalLogRecord:
    TIME = "time"
    EVENT = "event"
    ENTITY_ID = "entity_id"
    RELATED_ID = "related_id"
    STATUS = "status"
    COUNT = "count"
    PERMANENT = "permanent"
    IDENTIFIER = r"[a-f0-9]{32}"
    ABSENT = None
    MINIMUM_COUNT = 0

    @classmethod
    def create(cls, event, entity_id, related_id, status, count, permanent):
        payload: dict = {cls.TIME: datetime.now(timezone.utc).isoformat(), cls.EVENT: event}
        for key, identifier in ((cls.ENTITY_ID, entity_id), (cls.RELATED_ID, related_id)):
            if identifier is not cls.ABSENT and re.fullmatch(cls.IDENTIFIER, identifier):
                payload[key] = identifier
        if status in FileLogSink.STATUSES:
            payload[cls.STATUS] = status
        if type(count) is int and count >= cls.MINIMUM_COUNT:
            payload[cls.COUNT] = count
        if type(permanent) is bool:
            payload[cls.PERMANENT] = permanent
        return payload


class FileLogSink:
    """Allowlisted file writer; an Application may override open_handler to own retention."""

    STATUSES = frozenset(
        (
            SessionStatus.IDLE.value,
            SessionStatus.RUNNING.value,
            SessionStatus.DELETED.value,
            MessageStatus.QUEUED.value,
            MessageStatus.COMMITTED.value,
            MessageStatus.STREAMING.value,
            RunStatus.PENDING.value,
            RunStatus.COMPLETED.value,
            RunStatus.INTERRUPTED.value,
            RunStatus.CANCELLED.value,
            RunStatus.FAILED.value,
            RunStatus.PAUSED.value,
        )
    )

    def open_handler(self, path: Path):
        return _RaisingFileHandler(path, encoding=LoggingFormat.ENCODING)

    def __call__(self, logs: Path, event: str, fields: dict):
        _file_log_event(logs, event, handler_factory=self.open_handler, **fields)

    @staticmethod
    def handle_error(record: logging.LogRecord) -> None:
        # The caller emits a safe warning without the record or exception text.
        raise OSError(LoggingFormat.WRITE_FAILED)


class _RaisingFileHandler(logging.FileHandler):
    handleError = staticmethod(FileLogSink.handle_error)


def _file_log_event(
    logs: Path,
    event: str,
    *,
    handler_factory: Callable,
    entity_id: Optional[str] = LoggingFormat.ABSENT,
    related_id: Optional[str] = LoggingFormat.ABSENT,
    status: Optional[str] = LoggingFormat.ABSENT,
    count: Optional[int] = LoggingFormat.ABSENT,
    permanent: Optional[bool] = LoggingFormat.ABSENT,
) -> None:
    """Write only allowlisted operational fields; never accept arbitrary metadata.

    No global logger/handler cache and no open handle survives this call. This
    bounds descriptor lifetime and avoids duplicate handlers. Logs are best
    effort, not a durable audit transaction. The domain root must already exist.
    """
    if not re.fullmatch(LoggingFormat.EVENT_PATTERN, event):
        raise ValueError(LoggingFormat.INVALID_EVENT)
    if not logs.parent.is_dir():
        return
    payload = OperationalLogRecord.create(event, entity_id, related_id, status, count, permanent)
    handler = LoggingFormat.ABSENT
    logger = logging.Logger(LoggingFormat.LOGGER_NAME, level=logging.INFO)
    logger.propagate = LoggingFormat.PROPAGATE
    try:
        for directory in (logs, *logs.parents):
            if directory.is_symlink():
                raise OSError(LoggingFormat.LINKED_DIRECTORY)
        logs.mkdir(mode=LoggingFormat.DIRECTORY_MODE, exist_ok=LoggingFormat.CREATE_DIRECTORY)
        path = logs / LoggingFormat.FILE
        if path.is_symlink():
            raise OSError(LoggingFormat.LINKED_FILE)
        handler = handler_factory(path)
        handler.setFormatter(logging.Formatter(LoggingFormat.MESSAGE_FORMAT))
        logger.addHandler(handler)
        level = logging.ERROR if event.endswith(LoggingFormat.FAILED_SUFFIX) else logging.INFO
        logger.log(level, json.dumps(payload, ensure_ascii=LoggingFormat.ENSURE_ASCII))
    except OSError:
        warnings.warn(LoggingFormat.WRITE_WARNING, RuntimeWarning, stacklevel=LoggingFormat.WARNING_DEPTH)
    finally:
        if handler is not LoggingFormat.ABSENT:
            logger.removeHandler(handler)
            handler.close()
