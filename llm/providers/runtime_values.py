"""Provider initialization invariants and diagnostic/log serialization Owners."""

import json
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import ClassVar

from .request_values import SDKOperation, SDKRetryParameters
from llm.core.contracts import ExceptionType


class SDKRuntime:
    UNSPECIFIED = None
    LOCAL_COST_MAP = "LITELLM_LOCAL_MODEL_COST_MAP"
    BUNDLED_COST_MAP = "True"
    DEFAULT_MAX_RETRIES = "DEFAULT_MAX_RETRIES"
    SUPPRESS_DEBUG_INFO = "suppress_debug_info"
    SUPPRESS_BANNERS = True
    MODULE = SDKOperation.MODULE
    PACKAGES = (MODULE, "openai")
    VERSION_CACHE_SIZE = 1
    INITIALIZE = "initialize"
    SDK_IMPORT = "sdk_import"

    @classmethod
    def prepare_environment(cls):
        os.environ[cls.LOCAL_COST_MAP] = cls.BUNDLED_COST_MAP
        os.environ[cls.DEFAULT_MAX_RETRIES] = str(SDKRetryParameters.DISABLED)

    @classmethod
    def silence_banners(cls, sdk):
        setattr(sdk, cls.SUPPRESS_DEBUG_INFO, cls.SUPPRESS_BANNERS)

    @classmethod
    def reset_unselected_retries(cls, sdk):
        setattr(sdk, cls.DEFAULT_MAX_RETRIES, SDKRetryParameters.DISABLED)


class ProviderLogPaths:
    DEFAULT_PARENT = ".ish"
    DEFAULT_DIRECTORY = "llm-provider-logs"
    WORKSPACE_DIRECTORY = "logs"
    FILE_TEMPLATE = "providers-{pid}.log"
    CREATE_PARENTS = True
    EXIST_OK = True
    ENCODING = "utf-8"

    @classmethod
    def default(cls):
        return Path.home() / cls.DEFAULT_PARENT / cls.DEFAULT_DIRECTORY

    @classmethod
    def workspace(cls, workspace):
        return Path(workspace).absolute() / cls.WORKSPACE_DIRECTORY

    @classmethod
    def process(cls, directory):
        return directory / cls.FILE_TEMPLATE.format(pid=os.getpid())


class ProviderLogging:
    LOGGER = "llm.provider"
    NAMES = (
        "LiteLLM",
        "LiteLLM Router",
        "LiteLLM Proxy",
        "httpx",
        "httpcore",
        "dotenv",
        "py.warnings",
        LOGGER,
    )
    LOGGER_SEPARATOR = "."
    FORMAT = "%(asctime)s %(levelname)s %(message)s"
    MAX_OPEN_DESTINATIONS = 16
    INITIAL_FAILURES = 0
    FAILURE_INCREMENT = 1
    EVICT_NEWEST = False
    PROPAGATE = False
    CAPTURE_WARNINGS = True
    REFRESH = True
    KEEP_CONFIGURATION = False
    UNSPECIFIED = None


@dataclass(frozen=True, slots=True)
class ProviderLogSource:
    UNSPECIFIED: ClassVar[None] = None

    project_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    engine: str | None = None

    def to_dict(self):
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if getattr(self, item.name) is not self.UNSPECIFIED
        }


@dataclass(frozen=True, slots=True)
class ProviderLoggingStatus:
    failed_writes: int

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class ProviderLibraryLog:
    CODE: ClassVar[str] = "provider_library_log"

    code: str
    logger: str
    module: str
    line: int
    level: str

    @classmethod
    def from_record(cls, record):
        return cls(cls.CODE, record.name, record.module, record.lineno, record.levelname)

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


class ProviderLogRecord:
    DIAGNOSTIC = "provider_diagnostic"
    UNSPECIFIED = None
    NO_PATH = ""
    NO_LINE = 0
    ENSURE_ASCII = False

    @classmethod
    def trusted_payload(cls, record):
        return (
            getattr(record, cls.DIAGNOSTIC, cls.UNSPECIFIED)
            if record.name == ProviderLogging.LOGGER
            else cls.UNSPECIFIED
        )

    @classmethod
    def safe(cls, record, payload):
        return logging.LogRecord(
            record.name,
            record.levelno,
            cls.NO_PATH,
            cls.NO_LINE,
            json.dumps(payload, ensure_ascii=cls.ENSURE_ASCII),
            (),
            cls.UNSPECIFIED,
        )

    @classmethod
    def extra(cls, event):
        return {cls.DIAGNOSTIC: event.to_dict()}


class ProviderExceptionRecord:
    TYPE = ExceptionType.type.__name__
    FRAMES = "frames"
    STATUS_CODE = "status_code"
    ERRNO = "errno"
    ATTRIBUTE = "attribute"
    MODULE = "module"
    FILE = "file"
    LINE = "line"
    FUNCTION = "function"
    TYPE_TEMPLATE = ExceptionType.QUALIFIED_NAME

    @classmethod
    def type_name(cls, error):
        return cls.TYPE_TEMPLATE.format(module=type(error).__module__, name=type(error).__qualname__)

    @classmethod
    def create(cls, type_name, details):
        return {cls.TYPE: type_name, cls.FRAMES: [], **details}

    @classmethod
    def http_status(cls, record, status):
        record[cls.STATUS_CODE] = status

    @classmethod
    def os_error(cls, record, errno):
        record[cls.ERRNO] = errno

    @classmethod
    def attribute(cls, record, name):
        record[cls.ATTRIBUTE] = name

    @classmethod
    def module(cls, record, name):
        record[cls.MODULE] = name

    @classmethod
    def frame(cls, record, file, line, function):
        record[cls.FRAMES].append({cls.FILE: file, cls.LINE: line, cls.FUNCTION: function})
