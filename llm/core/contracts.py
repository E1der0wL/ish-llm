"""UI와 서비스가 공유하는 JSON 데이터 계약. 실행 권한이나 저장 책임은 갖지 않는다."""

from copy import deepcopy
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
import math
from pathlib import Path
from types import UnionType
from typing import Any, ClassVar, Optional, Union, get_args, get_origin, get_type_hints

from .models import ResourceKind, RunStatus, StepStatus, Run
from llm.core.json_data import JsonObject
from llm.errors import exception_chain, stable_error_code, get_error_attribute


class JsonValueError:
    UNION = "Value does not match the declared JSON type"
    NULL = "Expected null"
    ARRAY = "Expected an array"
    TUPLE = "Tuple length does not match the declared JSON type"
    OBJECT = "Expected an object"
    PATH = "Expected a path string"
    DATA = "Expected a data object"
    UNKNOWN_FIELDS = "Unknown data fields: {fields}"
    INVALID_VALUE = "Invalid {kind} value"
    UNSUPPORTED = "Unsupported data type: {kind}"


class DiagnosticObservation:
    CONTEXT_NAME = "llm_diagnostic_reads"
    ATTRIBUTE = "diagnostic"


# Observation-local recursion guard; no exception/Component type is privileged.
_diagnostic_reads = ContextVar(DiagnosticObservation.CONTEXT_NAME, default=())


def _encode(value):
    if is_dataclass(value):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return deepcopy(value)


def _decode(kind, value):
    origin, args = get_origin(kind), get_args(kind)
    if kind is Any:
        return deepcopy(value)
    if origin in (Union, UnionType):
        for candidate in args:
            try:
                return _decode(candidate, value)
            except (TypeError, ValueError):
                pass
        raise ValueError(JsonValueError.UNION)
    if kind is type(None):
        if value is not None:
            raise ValueError(JsonValueError.NULL)
        return None
    if origin in (list, tuple) or kind in (list, tuple):
        if not isinstance(value, list):
            raise ValueError(JsonValueError.ARRAY)
        if origin is tuple and args and args[-1] is not Ellipsis:
            if len(value) != len(args):
                raise ValueError(JsonValueError.TUPLE)
            items = [_decode(item_type, item) for item_type, item in zip(args, value)]
        else:
            items = [_decode(args[0] if args else Any, item) for item in value]
        return tuple(items) if origin is tuple or kind is tuple else items
    if origin is dict or kind is dict:
        if not isinstance(value, dict):
            raise ValueError(JsonValueError.OBJECT)
        return {_decode(args[0], k): _decode(args[1], v) for k, v in value.items()} if args else deepcopy(value)
    if kind is Path:
        if not isinstance(value, str):
            raise ValueError(JsonValueError.PATH)
        return Path(value)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if is_dataclass(kind):
        if not isinstance(value, dict):
            raise ValueError(JsonValueError.DATA)
        hints = get_type_hints(kind)
        names = {f.name for f in fields(kind) if f.init}
        if value.keys() - names:
            raise ValueError(JsonValueError.UNKNOWN_FIELDS.format(fields=sorted(value.keys() - names)))
        return kind(**{name: _decode(hints[name], item) for name, item in value.items()})
    if kind in (str, int, bool, float):
        if type(value) is not kind and not (kind is float and type(value) is int):
            raise ValueError(JsonValueError.INVALID_VALUE.format(kind=kind.__name__))
        return value
    raise TypeError(JsonValueError.UNSUPPORTED.format(kind=str(kind)))


class JsonValue:
    """명시적 JSON 변환. 딕셔너리 접근 별칭이나 실행/저장 동작을 추가하지 않는다."""

    __slots__ = ()

    def __post_init__(self):
        hints = get_type_hints(type(self))
        for item in fields(self):
            object.__setattr__(self, item.name, _decode(hints[item.name], _encode(getattr(self, item.name))))
        JsonObject.validate(self.to_dict())

    def to_dict(self) -> dict:
        value = _encode(self)
        JsonObject.validate(value)
        return value

    @classmethod
    def from_dict(cls, value: dict):
        return _decode(cls, value)


@dataclass(frozen=True, slots=True)
class ResourceRef(JsonValue):
    """도메인·컴포넌트가 소유한 대상 참조. 참조만으로 접근 권한을 부여하지 않는다."""
    EMPTY_IDENTITY: ClassVar[str] = "Resource kind and ID must be nonempty"

    kind: str
    id: str
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    step_id: Optional[str] = None
    component: Optional[str] = None
    version: Optional[str] = None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.kind.strip() or not self.id.strip():
            raise ValueError(self.EMPTY_IDENTITY)


class ErrorCategory(StrEnum):
    SERVICE = "service"
    ACCESS = "access"
    CONTRACT = "contract"
    RESOURCE = "resource"
    EXECUTION = "execution"
    INTERNAL = "internal"
    UNKNOWN = "unknown"

class Confidence(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"

class ErrorReason(StrEnum):
    """Known observation reasons; unfamiliar diagnostics remain open and unknown."""
    UNKNOWN = 'unknown'
    RATE_LIMIT = 'rate_limit'
    UNAVAILABLE = 'unavailable'
    AUTHENTICATION = 'authentication'
    PERMISSION = 'permission'
    TIMEOUT = 'timeout'
    CONNECTION = 'connection'
    REQUEST = 'request'
    CONTEXT = 'context'
    BUDGET = 'budget'
    FILE_MISSING = 'file_missing'
    EMPTY = 'empty'
    COMPLETION_INVALID = 'completion_invalid'
    COMPLETION_LENGTH = 'completion_length'
    COMPLETION_FILTERED = 'completion_filtered'
    COMPLETION_INCOMPLETE = 'completion_incomplete'
    COMPLETION_OUTPUT_LIMIT = 'completion_output_limit'
    COMPLETION_TOOL_LIMIT = 'completion_tool_limit'
    COMPLETION_ARGUMENT_LIMIT = 'completion_argument_limit'
    STEP_TIMEOUT = 'step_timeout'
    REQUEST_SIZE = 'request_size'
    DEPENDENCY = 'dependency'
    COMMAND = 'command'
    TOOL_FAILED = 'tool_failed'
    VALIDATION = 'validation'
    DISCONNECTED = 'disconnected'
    TOOL_ARGUMENTS = 'tool_arguments'
    TOOL_CONTRACT = 'tool_contract'
    LIFECYCLE = 'lifecycle'
    SANDBOX = 'sandbox'
    OPERATION_CONFLICT = 'operation_conflict'
    OPERATION_UNCERTAIN = 'operation_uncertain'
    AUTHORIZATION = 'authorization'
    APPROVAL = 'approval'
    RESPONSE = 'response'
    PARAMETER = 'parameter'
    INCOMPLETE = 'incomplete'
    OUTPUT = 'output'
    TOOLS = 'tools'
    ARGUMENTS = 'arguments'
    USAGE = 'usage'
    REVISION = 'revision'
    CHECKPOINT = 'checkpoint'
    CANCELLED = 'cancelled'


class DiagnosticCode(StrEnum):
    EXECUTION_FAILED = "execution_failed"


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ExceptionType:
    QUALIFIED_NAME: ClassVar[str] = "{module}.{name}"

    type: str

    @classmethod
    def from_exception(cls, error):
        return cls(cls.QUALIFIED_NAME.format(module=type(error).__module__, name=type(error).__qualname__))

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class DiagnosticExceptionDetails:
    """Fallback observation payload, serialized once at the Diagnostic boundary."""

    NO_BOUNDARY: ClassVar[None] = None

    exceptions: list[ExceptionType]
    boundary: Any = NO_BOUNDARY

    def to_dict(self):
        value = {DiagnosticExceptionDetails.exceptions.__name__: [item.to_dict() for item in self.exceptions]}
        if self.boundary is not self.NO_BOUNDARY:
            value[DiagnosticExceptionDetails.boundary.__name__] = self.boundary
        return value


@dataclass(frozen=True, slots=True)
class Diagnostic(JsonValue):
    """설명 가능한 진단. severity나 code는 재시도·복구·승인 권한이 아니다."""
    INVALID: ClassVar[str] = "Invalid diagnostic code or severity"

    code: str
    message: str
    severity: str = DiagnosticSeverity.ERROR.value
    source: Optional[ResourceRef] = None
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.code.strip() or self.severity not in tuple(DiagnosticSeverity):
            raise ValueError(self.INVALID)

    @classmethod
    def from_exception(cls, error: Exception, *, code=DiagnosticCode.EXECUTION_FAILED, source=None, boundary=None):
        # Diagnostic은 관찰 계약이다. Run의 신뢰 판별은 stable_error_code가 별도로 한다.
        known = stable_error_code(error)
        chain = list(exception_chain(error))
        # Re-entering for the same error uses the plain fallback. Do not walk
        # factories again: a chain of recursive properties would multiply reads.
        candidates = () if id(error) in _diagnostic_reads.get() else chain
        for current in candidates:
            supplied = None
            active = _diagnostic_reads.get()
            if id(current) not in active:
                token = _diagnostic_reads.set((*active, id(current)))
                try:
                    supplied = get_error_attribute(current, DiagnosticObservation.ATTRIBUTE)
                finally:
                    _diagnostic_reads.reset(token)
            # A property may call this converter itself. Re-entry skips only
            # that property, retaining the normal code/cause fallback below.
            if isinstance(supplied, cls) and (known is None or supplied.code == known):
                return cls(supplied.code, supplied.message, severity=supplied.severity,
                           source=supplied.source or source, details=supplied.details)
        details = DiagnosticExceptionDetails([ExceptionType.from_exception(item) for item in chain], boundary).to_dict()
        if known is not None:
            return cls(known, str(error), source=source, details=details)
        for current in chain:
            value = get_error_attribute(current, Diagnostic.code.__name__)
            if isinstance(value, str) and value.strip():
                return cls(str(value), str(error), source=source, details=details)
        return cls(code, str(error), source=source, details=details)

    @property
    def classification(self):
        """Derived observation, never another persisted failure or retry policy."""
        from .diagnostics import classify_diagnostic
        return classify_diagnostic(self)


class ActivityEventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_INTERRUPTED = "run.interrupted"
    RUN_PAUSED = "run.paused"
    RUN_RESUME_ABANDONED = "run.resume_abandoned"
    STEP_FAILED = "step.failed"


@dataclass(frozen=True, slots=True)
class ProjectActivityEvent(JsonValue):
    """확정된 lifecycle의 참조만 제공하는 파생 인덱스. 실행·복구의 근거가 아니다."""
    SEPARATOR: ClassVar[str] = "."
    RUN_NAME: ClassVar[str] = "{kind}" + SEPARATOR + "{status}"
    STATES: ClassVar[dict] = {
        ActivityEventType.RUN_STARTED: RunStatus.RUNNING.value,
        ActivityEventType.RUN_COMPLETED: RunStatus.COMPLETED.value,
        ActivityEventType.RUN_FAILED: RunStatus.FAILED.value,
        ActivityEventType.RUN_INTERRUPTED: RunStatus.INTERRUPTED.value,
        ActivityEventType.RUN_PAUSED: RunStatus.PAUSED.value,
        ActivityEventType.STEP_FAILED: StepStatus.FAILED.value,
    }
    ABANDONABLE: ClassVar[frozenset] = frozenset(Run.RESUMABLE_STATES)
    INVALID_LIFECYCLE: ClassVar[str] = "Invalid activity lifecycle"
    INVALID_REFERENCE: ClassVar[str] = "Invalid activity ownership reference"
    MISSING_TIMEZONE: ClassVar[str] = "Activity time requires a timezone"
    EMPTY_CODE: ClassVar[str] = "Activity code must be nonempty"

    time: str
    event: str
    source: ResourceRef
    status: Optional[str] = None
    code: Optional[str] = None

    @staticmethod
    def run_event(status):
        """Run log naming; activity validation still selects its allowed events."""
        return ProjectActivityEvent.RUN_NAME.format(kind=ResourceKind.RUN.value, status=status.value)

    def __post_init__(self):
        from datetime import datetime
        JsonValue.__post_init__(self)
        valid = (self.status in self.ABANDONABLE if self.event == ActivityEventType.RUN_RESUME_ABANDONED
                 else self.event in self.STATES and self.status == self.STATES[self.event])
        if not valid:
            raise ValueError(self.INVALID_LIFECYCLE)
        ref = self.source
        kind = self.event.split(self.SEPARATOR)[0]
        if (ref.kind != kind or not all((ref.project_id, ref.session_id, ref.run_id))
                or ref.id != (ref.step_id if kind == ResourceKind.STEP else ref.run_id)
                or kind == ResourceKind.RUN and ref.step_id is not None
                or ref.component is not None or ref.version is not None):
            raise ValueError(self.INVALID_REFERENCE)
        if datetime.fromisoformat(self.time).utcoffset() is None:
            raise ValueError(self.MISSING_TIMEZONE)
        if self.code is not None and not self.code.strip():
            raise ValueError(self.EMPTY_CODE)


@dataclass(frozen=True, slots=True)
class OperationProgress(JsonValue):
    """표시용 진행 정보. total=None은 전체량 미상이며 도메인 상태 전이를 대신하지 않는다."""
    MINIMUM: ClassVar[int] = 0
    UNKNOWN: ClassVar[None] = None
    EMPTY_PHASE: ClassVar[str] = "Progress phase must be nonempty"
    INVALID_COUNTS: ClassVar[str] = "Progress counts must be finite and nonnegative"
    INVALID_TOTAL: ClassVar[str] = "Progress requires completed <= total"

    source: ResourceRef
    phase: str
    completed: Optional[float] = UNKNOWN
    total: Optional[float] = UNKNOWN
    unit: Optional[str] = None
    message: str = ""

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.phase.strip():
            raise ValueError(self.EMPTY_PHASE)
        for value in (self.completed, self.total):
            if value is not self.UNKNOWN and (not math.isfinite(value) or value < self.MINIMUM):
                raise ValueError(self.INVALID_COUNTS)
        if self.total is not self.UNKNOWN and (self.completed is self.UNKNOWN or self.completed > self.total):
            raise ValueError(self.INVALID_TOTAL)


class DiagnosticRecordObservation:
    """Read only the requested optional fields of an opaque diagnostic record."""

    NO_TEXT = ""

    def __init__(self, value):
        self._value = value

    @property
    def message(self):
        return self._value.get(Diagnostic.message.__name__, self.NO_TEXT)

    @property
    def code(self):
        return self._value.get(Diagnostic.code.__name__, self.NO_TEXT)
