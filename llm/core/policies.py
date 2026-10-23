"""프로젝트의 명시적 JSON 정책 스키마·검증. 서비스나 실행 객체에는 의존하지 않는다."""

from copy import deepcopy
from dataclasses import dataclass, field as policy_field, fields
from typing import ClassVar
from enum import StrEnum
import json
import math
from jsonschema import Draft202012Validator


class UsageErrorCode(StrEnum):
    LIMIT = "usage_limit"
    CONFIGURATION = "usage_configuration"
    UNKNOWN = "usage_unknown"


class PolicySection(StrEnum):
    USAGE = "usage"


class RunPolicySnapshot:
    """Required admitted policy evidence; never default a missing Run snapshot."""

    KEY = "policies"

    @classmethod
    def read(cls, metadata):
        return metadata[cls.KEY]


class ContextMode(StrEnum):
    """Project context policy choices shared by Schema and runtime selection."""

    FULL = "full"
    RECENT = "recent"
    COMPLETED = "completed"
    RECENT_COMPLETED = "recent_completed"
    BUDGET = "budget"


def _declared_policy_schema(model):
    return {"type": "object", "additionalProperties": False,
            "properties": {item.name: deepcopy(item.metadata["schema"]) for item in fields(model)}}


def _validate_runtime_policy(value, *, allow_absent=True):
    # Runtime None denotes an absent optional limit. Stored explicit null still
    # passes through the stricter Project schema and is never silently removed.
    schema = value.schema()
    if not allow_absent and any(getattr(value, item.name) is None for item in fields(value)):
        raise ValueError(type(value).__name__ + " requires explicit runtime values")
    supplied = {item.name: getattr(value, item.name) for item in fields(value)
                if getattr(value, item.name) is not None}
    from .schema import validate_declared_fields
    validate_declared_fields(supplied, schema["properties"])


@dataclass(frozen=True, slots=True)
class UsageLimit:
    """A quota's two explicit ceilings and their public diagnostic labels."""

    max_calls: int | None
    max_tokens: int | None
    calls_label: str
    tokens_label: str

    @property
    def active(self):
        return self.max_calls is not None or self.max_tokens is not None


@dataclass(frozen=True, slots=True)
class UsagePolicy:
    """Read-only effective quota view; it never materializes omitted settings."""

    INVALID_COUNTER: ClassVar[str] = "Usage counter must be a nonempty name"
    counter: str | None = policy_field(default=None, metadata={"schema": {
        "type": "string", "description": "누적 토큰 예약 계산기 이름. Engine 입력 선택과 독립적이다", "minLength": 1}})
    max_calls: int | None = policy_field(default=None, metadata={"schema": {
        "type": ["integer", "null"], "description": "Run의 누적 모델 호출 상한", "minimum": 1}})
    max_tokens: int | None = policy_field(default=None, metadata={"schema": {
        "type": ["integer", "null"], "description": "Run 토큰 예약 상한", "minimum": 1}})
    project_max_calls: int | None = policy_field(default=None, metadata={"schema": {
        "type": ["integer", "null"], "description": "Project 기간 내 모델 호출 상한", "minimum": 1}})
    project_max_tokens: int | None = policy_field(default=None, metadata={"schema": {
        "type": ["integer", "null"], "description": "Project 기간 내 토큰 예약 상한", "minimum": 1}})
    period_seconds: float | None = policy_field(default=None, metadata={"schema": {
        "type": ["number", "null"], "description": "Project 사용량 집계 기간; null은 전체", "exclusiveMinimum": 0}})

    @classmethod
    def schema(cls, *, counters=()):
        value = _declared_policy_schema(cls)
        if counters:
            value["properties"][cls.counter.__name__]["enum"] = list(counters)
        return value

    @classmethod
    def from_dict(cls, value):
        # The Project Reader validates stored fields. This snapshot only reads them.
        return cls(**deepcopy(value))

    @classmethod
    def from_policies(cls, policies):
        return cls.from_dict(policies.get(PolicySection.USAGE, {}))

    @classmethod
    def from_run(cls, run):
        return cls.from_policies(RunPolicySnapshot.read(run.metadata))

    def __post_init__(self):
        _validate_runtime_policy(self)
        if self.counter is not None and not self.counter.strip():
            raise ValueError(self.INVALID_COUNTER)

    @property
    def run(self):
        return UsageLimit(self.max_calls, self.max_tokens,
                          type(self).max_calls.__name__, type(self).max_tokens.__name__)

    @property
    def project(self):
        return UsageLimit(self.project_max_calls, self.project_max_tokens,
                          type(self).project_max_calls.__name__, type(self).project_max_tokens.__name__)

    @property
    def requires_reservation(self):
        return self.max_tokens is not None or self.project_max_tokens is not None


@dataclass(frozen=True, slots=True)
class RunPolicy:
    """Explicit Session queue and whole-Run limits; None preserves absence."""

    max_queued: int | None = policy_field(default=None, metadata={"schema": {
        "type": ["integer", "null"], "minimum": 1, "description": "Session별 대기 요청 수 상한"}})
    timeout_seconds: float | None = policy_field(default=None, metadata={"schema": {
        "type": ["number", "null"], "exclusiveMinimum": 0, "description": "문맥 준비 포함 Run 기한(초)"}})
    max_capability_rounds: int | None = policy_field(default=None, metadata={"schema": {
        "type": ["integer", "null"], "minimum": 1, "description": "Engine capability 확장 탐색 횟수"}})

    @classmethod
    def schema(cls):
        return _declared_policy_schema(cls)

    def __post_init__(self):
        _validate_runtime_policy(self)


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Conversation selection choices, shared by Schema and runtime consumers."""

    mode: str | None = policy_field(default=None, metadata={"schema": {
        "type": "string", "enum": [mode.value for mode in ContextMode],
        "description": "엔진에 전달할 과거 대화 선택 방식"}})
    max_turns: int | None = policy_field(default=None, metadata={"schema": {
        "type": "integer", "minimum": 1, "description": "recent 계열의 과거 턴 수; 현재 입력은 별도"}})
    max_chars: int | None = policy_field(default=None, metadata={"schema": {
        "type": ["integer", "null"], "minimum": 1, "description": "현재 입력 포함 대화 본문 문자 상한"}})

    @classmethod
    def schema(cls):
        return _declared_policy_schema(cls)

    @staticmethod
    def validate_budget(mode, max_chars):
        if mode == ContextMode.BUDGET and max_chars is None:
            raise ValueError("Context budget mode requires max_chars")

    def __post_init__(self):
        _validate_runtime_policy(self)
        if self.mode in (ContextMode.RECENT, ContextMode.RECENT_COMPLETED) and self.max_turns is None:
            raise ValueError("Recent context requires explicit max_turns")
        self.validate_budget(self.mode, self.max_chars)


@dataclass(frozen=True)
class OutputBuffer:
    """Technical batching choices; a batch of one persists immediately."""

    IMMEDIATE_BATCH_SIZE: ClassVar[int] = 1
    batch_size: int = policy_field(default=IMMEDIATE_BATCH_SIZE, metadata={"schema": {
        "type": ["integer", "null"], "minimum": IMMEDIATE_BATCH_SIZE,
        "description": "델타 저장 묶음 크기; null은 백엔드 기본값"}})
    max_delay: float = policy_field(default=0.025, metadata={"schema": {
        "type": ["number", "null"], "exclusiveMinimum": 0,
        "description": "델타 저장 최대 대기(초); null은 백엔드 기본값"}})
    max_chars: int = policy_field(default=65536, metadata={"schema": {
        "type": ["integer", "null"], "minimum": 1,
        "description": "델타 저장 묶음 문자 상한; null은 백엔드 기본값"}})

    @classmethod
    def schema(cls):
        return _declared_policy_schema(cls)

    def __post_init__(self):
        _validate_runtime_policy(self, allow_absent=False)

    @property
    def immediate(self):
        return self.batch_size == self.IMMEDIATE_BATCH_SIZE

    def for_project(self, policy):
        """Stored null keeps the explicitly selected host batching value."""
        return type(self)(**{item.name: policy[item.name] if policy.get(item.name) is not None
                            else getattr(self, item.name) for item in fields(self)})



class ConstraintMode(StrEnum):
    FIXED = "fixed"
    SELECTABLE = "selectable"
    BOUNDED = "bounded"


class ArgumentConstraint:
    """Project-owned rule syntax and narrowing; Tool schemas remain independent."""

    def __init__(self, rule):
        from .models import ProjectConfig
        from .interactions import same_interaction_value
        ProjectConfig.validate_json(rule)
        if not isinstance(rule, dict):
            raise ValueError("Invalid Tool argument constraint")
        mode = rule.get("mode")
        if mode == ConstraintMode.FIXED:
            valid = rule.keys() == {"mode", "value"}
        elif mode == ConstraintMode.SELECTABLE:
            values = rule.get("values")
            valid = rule.keys() == {"mode", "values"} and isinstance(values, list) and bool(values)
            if valid:
                valid = all(not any(same_interaction_value(v, other) for other in values[:i])
                            for i, v in enumerate(values))
        elif mode == ConstraintMode.BOUNDED:
            bounds = {k: v for k, v in rule.items() if k != "mode"}
            valid = bool(bounds) and not bounds.keys() - {"minimum", "maximum"} and all(
                type(v) in (int, float) and math.isfinite(v) for v in bounds.values())
            if valid and bounds.keys() == {"minimum", "maximum"}:
                valid = bounds["minimum"] <= bounds["maximum"]
        else:
            valid = False
        if not valid:
            raise ValueError("Invalid Tool argument constraint")
        self._rule = deepcopy(rule)
        self.mode = ConstraintMode(mode)

    @property
    def choices(self):
        if self.mode == ConstraintMode.FIXED:
            return [deepcopy(self._rule["value"])]
        if self.mode == ConstraintMode.SELECTABLE:
            return deepcopy(self._rule["values"])
        return ()

    def value_schema(self):
        if self.mode == ConstraintMode.FIXED:
            return {"const": deepcopy(self._rule["value"])}
        if self.mode == ConstraintMode.SELECTABLE:
            return {"enum": deepcopy(self._rule["values"])}
        return {"type": "number", **{k: v for k, v in self._rule.items() if k != "mode"}}

    def narrow(self, parent):
        result = deepcopy(self._rule)
        if self.mode in (ConstraintMode.FIXED, ConstraintMode.SELECTABLE):
            validator = Draft202012Validator(parent.value_schema())
            if not all(validator.is_valid(v) for v in self.choices):
                raise ValueError("Child Tool argument constraint widens its parent")
        elif parent.mode == ConstraintMode.BOUNDED:
            for bound in ("minimum", "maximum"):
                if bound in parent._rule:
                    if bound not in result:
                        result[bound] = parent._rule[bound]
                    elif (bound == "minimum" and result[bound] < parent._rule[bound]
                          or bound == "maximum" and result[bound] > parent._rule[bound]):
                        raise ValueError("Child Tool argument constraint widens its parent")
        else:
            raise ValueError("Child constraint must preserve a fixed/selectable parent")
        return ArgumentConstraint(result)

    def to_dict(self):
        return deepcopy(self._rule)

    @staticmethod
    def schema():
        value_schema = {"x-schema-owner": "selected Tool parameter schema", "x-open-kind": "data"}
        rules = []
        for mode, properties, required in (
            (ConstraintMode.FIXED, {"value": value_schema}, ["value"]),
            (ConstraintMode.SELECTABLE, {"values": {"type": "array", "items": value_schema, "minItems": 1, "uniqueItems": True}}, ["values"]),
            (ConstraintMode.BOUNDED, {"minimum": {"type": "number"}, "maximum": {"type": "number"}}, []),
        ):
            rule = {"type": "object", "additionalProperties": False,
                    "properties": {"mode": {"const": mode.value}, **properties}, "required": ["mode", *required]}
            if mode == ConstraintMode.BOUNDED:
                rule["anyOf"] = [{"required": ["minimum"]}, {"required": ["maximum"]}]
            rules.append(rule)
        return {"oneOf": rules}


def validate_tool_constraints(constraints: dict) -> None:
    from .models import ProjectConfig
    ProjectConfig.validate_json(constraints)
    if not isinstance(constraints, dict):
        raise ValueError("argument_constraints must be an object")
    for name, fields in constraints.items():
        if not isinstance(name, str) or not name or not isinstance(fields, dict):
            raise ValueError("Tool constraints require named argument objects")
        for key, rule in fields.items():
            if not isinstance(key, str) or not key:
                raise ValueError("Invalid Tool argument constraint")
            ArgumentConstraint(rule)


def tool_constraints_schema():
    return {"type": "object", "propertyNames": {"minLength": 1}, "additionalProperties": {
        "type": "object", "propertyNames": {"minLength": 1}, "additionalProperties": ArgumentConstraint.schema()}}


def narrow_constraints(parent: dict, child: dict) -> dict:
    """Omission inherits; narrowing cannot widen or erase a parent rule."""
    validate_tool_constraints(child)
    result = deepcopy(parent)
    for name, fields in child.items():
        for key, value in fields.items():
            rule = ArgumentConstraint(value)
            old = parent.get(name, {}).get(key)
            if old is not None:
                rule = rule.narrow(ArgumentConstraint(old))
            result.setdefault(name, {})[key] = rule.to_dict()
    validate_tool_constraints(result)
    return result


def describe_policies() -> dict:
    """UI용 필드·설명을 포함한 독립 JSON Schema를 만든다."""
    def field(kind, description, **constraints):
        return {"type": kind, "description": description, **constraints}
    def section(properties):
        return {"type": "object", "additionalProperties": False, "properties": properties}
    return {"type": "object", "additionalProperties": False,
        "not": {"anyOf": [{"required": [name]} for name in ("completion", "provider_retry")]},
        "properties": {
        "approval": section({
            "enabled": field("boolean", "프로젝트 자동 승인 규칙 적용"),
            "risk_scheme": field("string", "Application이 소유한 위험도 척도 identity", minLength=1),
            "rules": field("array", "카테고리·위험도별 승인 규칙; 불확실한 재실행은 제외", items={
                "type": "object", "additionalProperties": False, "required": ["id", "category", "max_risk"],
                "properties": {"id": {"type": "string", "minLength": 1},
                    "category": {"type": "string", "minLength": 1},
                    "max_risk": {"type": "integer", "minimum": 0}}})}),
        "tools": section({
            "allowed_tools": field(["array", "null"], "Project에서 허용할 Tool 이름", uniqueItems=True,
                items={"type": "string", "minLength": 1}),
            "max_calls": field(["integer", "null"], "Run 전체 Tool 호출 한도", minimum=1),
            "timeout_seconds": field(["number", "null"], "각 Tool 호출 기한; Engine은 더 좁힐 수 있다", exclusiveMinimum=0),
            "max_output_chars": field(["integer", "null"], "각 Tool 결과 문자 한도", minimum=1),
            "argument_constraints": tool_constraints_schema()}),
        "context": ContextPolicy.schema(),
        "output": OutputBuffer.schema(),
        "tool_retry": section({
            "max_retries": field(["integer", "null"], "조건부 Tool 재시도; null은 비활성화", minimum=0),
            "delay_seconds": field(["number", "null"], "Tool 재시도 대기; null은 비활성화", minimum=0)}),
        PolicySection.USAGE: UsagePolicy.schema(),
        "run": RunPolicy.schema()}}


def normalize_policies(value: dict) -> dict:
    """명시된 키만 검증한다. 생략한 정책과 leaf는 그대로 생략한다."""
    schema = describe_policies()
    json.dumps(value, allow_nan=False)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.path)
        raise ValueError(f"Invalid project policy {path}: {error.message}")
    result = deepcopy(value)
    validate_tool_constraints(result.get("tools", {}).get("argument_constraints", {}))
    # 존재하는 키만 검증·보존한다. missing은 정책 활성화나 기본값 생성을 뜻하지 않는다.
    for name, section in schema["properties"].items():
        for key, spec in section["properties"].items():
            number = result.get(name, {}).get(key)
            kind = spec["type"]
            if (kind == "integer" or isinstance(kind, list) and "integer" in kind):
                if number is not None and type(number) is not int:
                    raise ValueError(f"Policy {name}.{key} requires an integer")
    rule_ids = [rule["id"] for rule in result.get("approval", {}).get("rules", [])]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("Approval rule IDs must be unique")
    approval = result.get("approval", {})
    if approval.get("rules") and not str(approval.get("risk_scheme", "")).strip():
        raise ValueError("Approval rules require risk_scheme")
    if any(type(rule["max_risk"]) is not int for rule in approval.get("rules", [])):
        raise ValueError("Approval max_risk must be a nonnegative integer")
    context = result.get("context", {})
    ContextPolicy.validate_budget(context.get("mode"), context.get("max_chars"))
    UsagePolicy.from_policies(result)
    return result
