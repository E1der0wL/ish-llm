"""공급자 초기화·진단의 단일 경계. 숨은 SDK 기본 재시도는 항상 끈다."""

from llm.providers.diagnostic_values import ProviderDiagnosticText

from llm.core.contracts import ErrorReason, DiagnosticSeverity
from .diagnostic_values import ProviderRequestFailure

import importlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from collections.abc import Mapping
from functools import lru_cache
from importlib.metadata import version, PackageNotFoundError
from urllib.parse import urlsplit, urlunsplit
from contextlib import contextmanager
from contextvars import ContextVar
from collections import OrderedDict
from pathlib import Path

_observer = ContextVar("provider_diagnostics", default=None)
_directory = ContextVar("provider_log_directory", default=None)
_source = ContextVar("provider_log_source", default=None)
_options = ContextVar("provider_log_options", default=None)
_lock = threading.RLock()
_handler = None
_sdk = None
_names = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "httpx", "httpcore", "dotenv", "py.warnings", "llm.provider")


@dataclass(frozen=True, slots=True)
class ProviderDiagnostics:
    """Host 로깅 설정. 실행 정책/SDK 인자가 아니며 미설정이면 상세 내용을 수집하지 않는다.

    error_details는 서버 오류의 message/type/code/param 및 endpoint를 기록한다.
    request_summary는 본문 없이 역할/길이/Tool 대응 개수만 기록한다.
    max_detail_chars는 명시했을 때만 개별 상세 문자열을 자른다. 원본 요청/응답은 저장하지 않는다.
    """

    error_details: bool | None = None
    request_summary: bool | None = None
    max_detail_chars: int | None = None

    def __post_init__(self):
        for value in (self.error_details, self.request_summary):
            if value is not None and type(value) is not bool:
                raise TypeError("Provider diagnostic flags must be boolean or None")
        if self.max_detail_chars is not None and (type(self.max_detail_chars) is not int or self.max_detail_chars < 1):
            raise ValueError("max_detail_chars must be a positive integer")


@contextmanager
def diagnostic_context(**source):
    """호출/Step 관찰 문맥만 확장한다. 실행 권한이나 도메인 상태를 만들지 않는다."""
    values = {**(_source.get() or {}), **source}
    token = _source.set(values)
    try:
        yield values
    finally:
        _source.reset(token)


@lru_cache(maxsize=1)
def _sdk_versions():
    result = {}
    for name in ("litellm", "openai"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            pass
    return result


def _request_details(request):
    """진단용 구조 요약. 값/Tool 이름/ID/본문/인증 헤더는 복사하지 않는다."""
    result = {}
    if isinstance(request.get("model"), str):
        result["model"] = request["model"]
    options = _options.get()
    if options is None:
        return result
    if options.error_details is True and isinstance(request.get("api_base"), str):
        try:
            url = urlsplit(request["api_base"])
            # 사용자 정보/query/fragment는 endpoint identity에 포함하지 않는다.
            host = url.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            result["endpoint"] = urlunsplit((url.scheme, host + (f":{url.port}" if url.port else ""), url.path, "", ""))
        except ValueError:
            pass
    if options.request_summary is not True:
        return result
    messages = request.get("messages")
    if not isinstance(messages, (list, tuple)):
        return result
    rows, pending, seen, duplicates, unmatched, out_of_order = [], set(), set(), 0, 0, 0
    for message in messages:
        if not isinstance(message, Mapping):
            rows.append({"valid_object": False})
            continue
        role = message.get("role")
        content = message.get("content")
        calls = message.get("tool_calls")
        row = {"role": role if role in ("system", "developer", "user", "assistant", "tool", "function") else "unknown",
               "content_type": "text" if isinstance(content, str) else "parts" if isinstance(content, list) else "null" if content is None else "invalid"}
        if isinstance(content, str):
            row["content_chars"] = len(content)
        if isinstance(content, list):
            row["content_parts"] = len(content)
        if pending and role != "tool":
            out_of_order += 1
        if isinstance(calls, list):
            row["tool_calls"] = len(calls)
            for call in calls:
                key = call.get("id") if isinstance(call, Mapping) else None
                if isinstance(key, str) and key:
                    duplicates += int(key in seen)
                    seen.add(key)
                    pending.add(key)
        if role == "tool":
            key = message.get("tool_call_id")
            if isinstance(key, str) and key in pending:
                pending.remove(key)
            else:
                unmatched += 1
        rows.append(row)
    result["request_summary"] = {"messages": rows, "message_count": len(rows),
        "tool_count": len(request["tools"]) if isinstance(request.get("tools"), (list, tuple)) else 0,
        "unresolved_tool_calls": len(pending), "unmatched_tool_results": unmatched,
        "duplicate_tool_call_ids": duplicates, "out_of_order_messages": out_of_order}
    return result


def request_details(request):
    """진단용 객체가 잘못되어도 실제 SDK 요청/취소 결과를 바꾸지 않는다."""
    try:
        return _request_details(request)
    except Exception:
        return {}


def _error_details(error):
    """서버가 제공한 식별자만 기본 기록하고 상세 오류 문자열은 opt-in으로 수집한다."""
    result = {}
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None)
    request_id = getattr(error, "request_id", None)
    if isinstance(request_id, str):
        result["request_id"] = request_id
    elif hasattr(headers, "items"):
        for key, value in headers.items():
            if str(key).lower() in ("x-request-id", "request-id", "x-amzn-requestid") and isinstance(value, str):
                result["request_id"] = value
                break
    options = _options.get()
    if options is None or options.error_details is not True:
        return result
    body = getattr(error, "body", None)
    if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping):
        body = body["error"]
    detail = {}
    for key in ("message", "type", "code", "param"):
        value = body.get(key) if isinstance(body, Mapping) else None
        if isinstance(value, str):
            detail[key] = value[:options.max_detail_chars] if options.max_detail_chars is not None else value
    if "message" not in detail:
        value = str(error)
        detail["message"] = value[:options.max_detail_chars] if options.max_detail_chars is not None else value
    result["provider_error"] = detail
    return result


class _FileHandler(logging.FileHandler):
    def __init__(self, path, owner):
        self.owner = owner
        # Provider diagnostics are append-only; retention belongs to the Application.
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
def logging_scope(workspace, *, project_id=None, session_id=None, run_id=None, engine=None, diagnostics=None):
    """동시에 사용하는 백엔드가 서로의 로그 경로를 덮어쓰지 않게 한다."""
    directory = Path(workspace).absolute() / "logs"
    same_workspace = _directory.get() == directory
    if diagnostics is not None and not isinstance(diagnostics, ProviderDiagnostics):
        raise TypeError("diagnostics must be ProviderDiagnostics")
    token = _directory.set(directory)
    option_token = _options.set(diagnostics if diagnostics is not None else _options.get() if same_workspace else None)
    source_token = _source.set({**(_source.get() or {} if same_workspace else {}), **{key: value for key, value in (
        ("project_id", project_id), ("session_id", session_id), ("run_id", run_id),
        ("engine", engine)) if value is not None}})
    try:
        configure_logging()
        yield
    finally:
        _source.reset(source_token)
        _options.reset(option_token)
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
                    # Observation must not rewrite evidence used by recovery.
                    handler(type(event).from_dict(event.to_dict()))
                except Exception:
                    pass
    token = _observer.set(observe)
    try:
        yield
    finally:
        _observer.reset(token)


def diagnostic(code, *, severity=DiagnosticSeverity.INFO.value, **details):
    from llm.core.contracts import Diagnostic
    event = Diagnostic(code, ProviderDiagnosticText.describe(code), severity=severity,
                       details={**(_source.get() or {}), **details})
    try:
        configure_logging()
        logging.getLogger("llm.provider").log(getattr(logging, severity.upper()), event.message,
                                             extra={"provider_diagnostic": event.to_dict()})
    except Exception:
        if _handler is not None:
            _handler.failed_writes += 1
    callback = _observer.get()
    if callback is not None:
        try:
            callback(event)  # diagnostic_scope supplies a separate copy to each observer.
        except Exception:
            pass
    return event


def diagnostic_failure(error, *, operation, stage, **details):
    """실패 위치와 명시적으로 선택한 상세 오류를 기록한다. 진단 실패는 실행 오류를 바꾸지 않는다.

    SDK 예외 메시지에는 인증·본문이 섞일 수 있으므로 포맷된 traceback 대신
    파일/행/함수를 수집한다. 원문 오류 메시지는 error_details를 켰을 때만 추가한다.
    사용자 요청의 성공·재시도·취소 판단에는 관여하지 않는다.
    """
    try:
        from llm.errors import exception_chain
        from .requests import error_code
        chain = []
        request_failure = None
        http_seen = False
        for current in exception_chain(error):
            entry = {"type": f"{type(current).__module__}.{type(current).__qualname__}", "frames": [],
                     **_error_details(current)}
            status = getattr(current, "status_code", None)
            if type(status) is int:
                entry["status_code"] = status
                if not http_seen and status == 400:
                    # Interpret only this cause, even when detailed logging is disabled.
                    body = getattr(current, "body", None)
                    if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping):
                        body = body["error"]
                    message = body.get("message") if isinstance(body, Mapping) else None
                    text = (message if isinstance(message, str) else str(current)).lower()
                    reason = (ErrorReason.PARAMETER if any(term in text for term in (
                        "unsupported parameter", "unknown parameter", "unrecognized request argument")) else
                        ErrorReason.CONTEXT if any(term in text for term in (
                        "context_length_exceeded", "maximum context length", "context length exceeded",
                        "context window exceeded", "exceeds the context", "exceeds context",
                        "context window limit", "too many tokens", "prompt is too long")) else None)
                    if reason:
                        request_failure = ProviderRequestFailure(reason, len(chain), status)
                http_seen = True
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
        return diagnostic(error_code(error), severity=DiagnosticSeverity.ERROR.value, operation=operation, boundary="provider",
                          **(request_failure.details() if request_failure else {}),
                          stage=stage, exceptions=chain, sdk_versions=_sdk_versions(), **details)
    except Exception:
        # 파일/observer/예외 객체가 고장나도 원래 실패와 cleanup 순서를 보존한다.
        if _handler is not None:
            _handler.failed_writes += 1
        return None
