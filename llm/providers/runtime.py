"""공급자 초기화·진단의 단일 경계. 숨은 SDK 기본 재시도는 항상 끈다."""

import importlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from collections import OrderedDict
from pathlib import Path
from logging.handlers import RotatingFileHandler

_observer = ContextVar("provider_diagnostics", default=None)
_directory = ContextVar("provider_log_directory", default=None)
_lock = threading.RLock()
_handler = None
_sdk = None
_names = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "httpx", "httpcore", "dotenv", "py.warnings", "llm.provider")


class _FileHandler(RotatingFileHandler):
    def __init__(self, path, owner):
        self.owner = owner
        # No retention policy was supplied: never rotate/delete provider diagnostics.
        super().__init__(path, encoding="utf-8")

    def handleError(self, record):
        # 디스크 장애에서도 logging 자체의 traceback을 TUI에 내보내지 않는다.
        self.owner.failed_writes += 1


class _DiagnosticsHandler(logging.Handler):
    """임의 SDK 메시지는 비밀값을 포함할 수 있어 위치/등급만 기록한다."""

    def __init__(self):
        super().__init__()
        self.files = OrderedDict()
        self.file_lock = threading.RLock()
        self.failed_writes = 0

    def emit(self, record):
        directory = _directory.get() or Path.home() / ".ish" / "llm-provider-logs"
        # SDK import 중 다른 스레드의 로그와 lock 순서가 뒤집히지 않게 별도 잠금을 쓴다.
        with self.file_lock:
            path = directory / f"providers-{os.getpid()}.log"
            if path not in self.files:
                if len(self.files) >= 16:
                    _, previous = self.files.popitem(last=False)
                    previous.close()
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    handler = _FileHandler(path, self)
                except OSError:
                    self.failed_writes += 1
                    return
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
                self.files[path] = handler
            self.files.move_to_end(path)
            # SDK 원문/traceback/args를 전달하지 않는다. 신뢰한 진단도 이 모듈에서만 생성한다.
            payload = getattr(record, "provider_diagnostic", None) if record.name == "llm.provider" else None
            if payload is None:
                payload = {"code": "provider_library_log", "logger": record.name,
                           "module": record.module, "line": record.lineno, "level": record.levelname}
            safe = logging.LogRecord(record.name, record.levelno, "", 0, json.dumps(payload, ensure_ascii=False), (), None)
            self.files[path].emit(safe)


def logging_status():
    """로그 저장소 장애 관찰. 실패 카운터는 진단이며 도메인 저장 성공을 뜻하지 않는다."""
    return {"failed_writes": _handler.failed_writes if _handler is not None else 0}


def configure_logging(workspace=None, *, refresh=False):
    """현재 실행 문맥의 진단 목적지. 다른 workspace의 비동기 작업과 분리한다."""
    global _handler
    if workspace is not None:
        _directory.set(Path(workspace).absolute() / "logs")
    # 느린 최초 SDK import가 같은 lock을 소유해도 이벤트 루프의 진단은 대기하지 않는다.
    if _handler is not None and not refresh:
        return
    with _lock:
        if _handler is not None and not refresh:
            return
        if _handler is None:
            _handler = _DiagnosticsHandler()
        names = set(_names)
        names.update(name for name in logging.Logger.manager.loggerDict
                     if any(name.startswith(prefix + ".") for prefix in _names))
        for name in names:
            logger = logging.getLogger(name)
            logger.handlers[:] = [_handler]
            logger.setLevel(logging.INFO if name == "llm.provider" else logging.WARNING)
            logger.propagate = False
        logging.captureWarnings(True)


@contextmanager
def logging_scope(workspace):
    """동시에 사용하는 백엔드가 서로의 로그 경로를 덮어쓰지 않게 한다."""
    token = _directory.set(Path(workspace).absolute() / "logs")
    try:
        configure_logging()
        yield
    finally:
        _directory.reset(token)


def litellm_sdk():
    """숨은 기본 retry는 0. 요청에 명시한 retry 값은 변경하지 않는다."""
    global _sdk
    with _lock:
        # 초기화의 외부 비용표 fetch를 격리한다. 추론 옵션/Project 설정 기본값이 아니다.
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        os.environ["DEFAULT_MAX_RETRIES"] = "0"
        if _sdk is None:
            configure_logging(refresh=True)
            _sdk = importlib.import_module("litellm")
            # Host owns the terminal: SDK help banners must not corrupt its input UI.
            _sdk.suppress_debug_info = True
            configure_logging(refresh=True)
        # LiteLLM의 `max_retries or DEFAULT_MAX_RETRIES` 호환성 규칙.
        # 외부 코드가 전역값을 변경한 경우에도 호출 진입 시 복구한다.
        _sdk.DEFAULT_MAX_RETRIES = 0
        return _sdk


@contextmanager
def diagnostic_scope(callback):
    """동기 callback은 UI/보고서 수집용이다. 예외가 실행 결과를 바꾸지 않게 한다."""
    previous = _observer.get()
    def observe(event):
        for handler in (callback, previous):
            if handler is not None:
                try:
                    handler(event)
                except Exception:
                    pass
    token = _observer.set(observe)
    try:
        yield
    finally:
        _observer.reset(token)


def diagnostic(code, *, severity="info", **details):
    from llm.core.contracts import Diagnostic
    event = Diagnostic(code, code.replace("_", " "), severity=severity, details=details)
    configure_logging()
    logging.getLogger("llm.provider").log(getattr(logging, severity.upper()), event.message,
                                         extra={"provider_diagnostic": event.to_dict()})
    callback = _observer.get()
    if callback is not None:
        try:
            callback(event)
        except Exception:
            pass
    return event
