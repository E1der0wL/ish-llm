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
_source = ContextVar("provider_log_source", default=None)
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
def logging_scope(workspace, *, project_id=None, session_id=None, run_id=None, engine=None):
    """동시에 사용하는 백엔드가 서로의 로그 경로를 덮어쓰지 않게 한다."""
    token = _directory.set(Path(workspace).absolute() / "logs")
    source_token = _source.set({key: value for key, value in (
        ("project_id", project_id), ("session_id", session_id), ("run_id", run_id),
        ("engine", engine)) if value is not None})
    try:
        configure_logging()
        yield
    finally:
        _source.reset(source_token)
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
            try:
                _sdk = importlib.import_module("litellm")
            except Exception as error:
                diagnostic_failure(error, operation="initialize", stage="sdk_import")
                raise
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
    event = Diagnostic(code, code.replace("_", " "), severity=severity,
                       details={**(_source.get() or {}), **details})
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


def diagnostic_failure(error, *, operation, stage, **details):
    """원문/locals/source 없이 실패 위치를 기록한다. 진단 실패는 실행 오류를 바꾸지 않는다.

    SDK 예외 메시지에는 인증·본문이 섞일 수 있으므로 포맷된 traceback 대신
    파일/행/함수만 수집한다. 사용자 요청의 성공·재시도·취소 판단에는 관여하지 않는다.
    """
    try:
        from llm.errors import exception_chain
        from .requests import error_code
        chain = []
        for current in exception_chain(error):
            entry = {"type": f"{type(current).__module__}.{type(current).__qualname__}", "frames": []}
            status = getattr(current, "status_code", None)
            if type(status) is int:
                entry["status_code"] = status
            if isinstance(current, OSError) and type(current.errno) is int:
                entry["errno"] = current.errno
            if isinstance(current, AttributeError) and isinstance(current.name, str) and current.name.isidentifier():
                entry["attribute"] = current.name
            if isinstance(current, ImportError) and isinstance(current.name, str):
                # ImportError.name은 Python import identity다. args/message/path는 기록하지 않는다.
                if all(part.isidentifier() for part in current.name.split(".")):
                    entry["module"] = current.name
            trace = current.__traceback__
            while trace is not None:
                code = trace.tb_frame.f_code
                entry["frames"].append({"file": code.co_filename, "line": trace.tb_lineno,
                                        "function": code.co_name})
                trace = trace.tb_next
            chain.append(entry)
        return diagnostic(error_code(error), severity="error", operation=operation,
                          stage=stage, exceptions=chain, **details)
    except Exception:
        # 파일/observer/예외 객체가 고장나도 원래 실패와 cleanup 순서를 보존한다.
        if _handler is not None:
            _handler.failed_writes += 1
        return None
