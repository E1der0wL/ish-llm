"""UI와 서비스가 공유하는 JSON 데이터 계약. 실행 권한이나 저장 책임은 갖지 않는다."""

from copy import deepcopy
from dataclasses import field, fields, is_dataclass
from enum import Enum
import math
from pathlib import Path
from typing import Any, Optional, Union, get_args, get_origin, get_type_hints

from llm.compat import dataclass
from .models import ProjectConfig
from llm.errors import exception_chain, stable_error_code


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
    if origin is Union:
        for candidate in args:
            try:
                return _decode(candidate, value)
            except (TypeError, ValueError):
                pass
        raise ValueError("Value does not match the declared JSON type")
    if kind is type(None):
        if value is not None:
            raise ValueError("Expected null")
        return None
    if origin in (list, tuple) or kind in (list, tuple):
        if not isinstance(value, list):
            raise ValueError("Expected an array")
        items = [_decode(args[0] if args else Any, item) for item in value]
        return tuple(items) if origin is tuple or kind is tuple else items
    if origin is dict or kind is dict:
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return {_decode(args[0], k): _decode(args[1], v) for k, v in value.items()} if args else deepcopy(value)
    if kind is Path:
        if not isinstance(value, str):
            raise ValueError("Expected a path string")
        return Path(value)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if is_dataclass(kind):
        if not isinstance(value, dict):
            raise ValueError("Expected a data object")
        hints = get_type_hints(kind)
        names = {f.name for f in fields(kind) if f.init}
        if value.keys() - names:
            raise ValueError("Unknown data fields: " + str(sorted(value.keys() - names)))
        return kind(**{name: _decode(hints[name], item) for name, item in value.items()})
    if kind in (str, int, bool, float):
        if type(value) is not kind and not (kind is float and type(value) is int):
            raise ValueError("Invalid " + kind.__name__ + " value")
        return value
    raise TypeError("Unsupported data type: " + str(kind))


class JsonValue:
    """명시적 JSON 변환. 딕셔너리 접근 별칭이나 실행/저장 동작을 추가하지 않는다."""

    __slots__ = ()

    def __post_init__(self):
        hints = get_type_hints(type(self))
        for item in fields(self):
            object.__setattr__(self, item.name, _decode(hints[item.name], _encode(getattr(self, item.name))))
        ProjectConfig.validate_settings(self.to_dict())

    def to_dict(self) -> dict:
        value = _encode(self)
        ProjectConfig.validate_settings(value)
        return value

    @classmethod
    def from_dict(cls, value: dict):
        return _decode(cls, value)


@dataclass(frozen=True, slots=True)
class ResourceRef(JsonValue):
    """도메인·컴포넌트가 소유한 대상 참조. 참조만으로 접근 권한을 부여하지 않는다."""
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
            raise ValueError("Resource kind and ID must be nonempty")


@dataclass(frozen=True, slots=True)
class Diagnostic(JsonValue):
    """설명 가능한 진단. severity나 code는 재시도·복구·승인 권한이 아니다."""
    code: str
    message: str
    severity: str = "error"
    source: Optional[ResourceRef] = None
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.code.strip() or self.severity not in ("info", "warning", "error"):
            raise ValueError("Invalid diagnostic code or severity")

    @classmethod
    def from_exception(cls, error: Exception, *, code="execution_failed", source=None):
        # Diagnostic은 관찰 계약이다. Run의 신뢰 판별은 stable_error_code가 별도로 한다.
        known = stable_error_code(error)
        if known is not None:
            return cls(known, str(error), source=source)
        for current in exception_chain(error):
            value = getattr(current, "code", None)
            if isinstance(value, str) and value.strip():
                return cls(str(value), str(error), source=source)
        return cls(code, str(error), source=source)


@dataclass(frozen=True, slots=True)
class ProjectActivityEvent(JsonValue):
    """확정된 lifecycle의 참조만 제공하는 파생 인덱스. 실행·복구의 근거가 아니다."""
    time: str
    event: str
    source: ResourceRef
    status: Optional[str] = None
    code: Optional[str] = None

    def __post_init__(self):
        from datetime import datetime
        JsonValue.__post_init__(self)
        states = {"run.started": "running", "run.completed": "completed",
                  "run.failed": "failed", "run.interrupted": "interrupted",
                  "run.paused": "paused", "step.failed": "failed"}
        if self.event not in states or self.status != states[self.event]:
            raise ValueError("Invalid activity lifecycle")
        ref = self.source
        kind = self.event.split(".")[0]
        if (ref.kind != kind or not all((ref.project_id, ref.session_id, ref.run_id))
                or ref.id != (ref.step_id if kind == "step" else ref.run_id)
                or kind == "run" and ref.step_id is not None
                or ref.component is not None or ref.version is not None):
            raise ValueError("Invalid activity ownership reference")
        if datetime.fromisoformat(self.time).utcoffset() is None:
            raise ValueError("Activity time requires a timezone")
        if self.code is not None and not self.code.strip():
            raise ValueError("Activity code must be nonempty")


@dataclass(frozen=True, slots=True)
class OperationProgress(JsonValue):
    """표시용 진행 정보. total=None은 전체량 미상이며 도메인 상태 전이를 대신하지 않는다."""
    source: ResourceRef
    phase: str
    completed: Optional[float] = None
    total: Optional[float] = None
    unit: Optional[str] = None
    message: str = ""

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.phase.strip():
            raise ValueError("Progress phase must be nonempty")
        for value in (self.completed, self.total):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("Progress counts must be finite and nonnegative")
        if self.total is not None and (self.completed is None or self.completed > self.total):
            raise ValueError("Progress requires completed <= total")
