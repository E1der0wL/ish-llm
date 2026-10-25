"""프로젝트의 명시적 JSON 정책 스키마·검증. 서비스나 실행 객체에는 의존하지 않는다."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field as policy_field, fields, replace
from typing import ClassVar
from enum import StrEnum
import json
import math
from jsonschema import Draft202012Validator
from .json_data import JsonObject
from .schema import SchemaKeyword, SchemaType, SchemaBoolean, SchemaExtension, OpenSchemaKind


class UsageErrorCode(StrEnum):
    LIMIT = "usage_limit"
    CONFIGURATION = "usage_configuration"
    UNKNOWN = "usage_unknown"


class PolicySection(StrEnum):
    APPROVAL = "approval"
    TOOLS = "tools"
    CONTEXT = "context"
    OUTPUT = "output"
    TOOL_RETRY = "tool_retry"
    USAGE = "usage"
    RUN = "run"


class PolicyDeclaration:
    SCHEMA = "schema"
    ALLOW_ABSENT = True
    REQUIRE_VALUES = False
    EXPLICIT_VALUES = " requires explicit runtime values"
    REMOVED = ("completion", "provider_retry")
    PATH_SEPARATOR = "."
    INVALID = "Invalid project policy {path}: {message}"
    INTEGER_REQUIRED = "Policy {name}.{key} requires an integer"


class PolicyValue:
    """A detached runtime view of an explicitly validated policy section."""

    __slots__ = ()

    @classmethod
    def from_dict(cls, value):
        normalize_policies({cls.SECTION: value})
        return cls(**deepcopy(value))

    @classmethod
    def from_policies(cls, values):
        return cls.from_dict(values.get(cls.SECTION, {}))

    @classmethod
    def from_run(cls, run):
        return cls.from_policies(RunPolicySnapshot.read(run.metadata))

    @classmethod
    def schema(cls):
        return _declared_policy_schema(cls)

    def to_dict(self):
        return asdict(self)


class RunPolicySnapshot:
    """Required admitted policy evidence; never default a missing Run snapshot."""

    KEY = "policies"

    @classmethod
    def read(cls, metadata):
        return metadata[cls.KEY]

    @classmethod
    def store(cls, metadata, policies):
        """Write a newly admitted snapshot; never repair a stored Run on read."""
        metadata[cls.KEY] = deepcopy(policies)


class ContextMode(StrEnum):
    """Project context policy choices shared by Schema and runtime selection."""

    FULL = "full"
    RECENT = "recent"
    COMPLETED = "completed"
    RECENT_COMPLETED = "recent_completed"
    BUDGET = "budget"


def _declared_policy_schema(model):
    return {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
            SchemaKeyword.PROPERTIES: {item.name: deepcopy(item.metadata[PolicyDeclaration.SCHEMA]) for item in fields(model)}}


def _validate_runtime_policy(value, *, allow_absent=PolicyDeclaration.ALLOW_ABSENT):
    # Runtime None denotes an absent optional limit. Stored explicit null still
    # passes through the stricter Project schema and is never silently removed.
    schema = value.schema()
    if not allow_absent and any(getattr(value, item.name) is None for item in fields(value)):
        raise ValueError(type(value).__name__ + PolicyDeclaration.EXPLICIT_VALUES)
    supplied = {item.name: getattr(value, item.name) for item in fields(value)
                if getattr(value, item.name) is not None}
    from .schema import validate_declared_fields
    validate_declared_fields(supplied, schema[SchemaKeyword.PROPERTIES])


@dataclass(frozen=True, slots=True)
class UsageLimit:
    """A quota's two explicit ceilings and their public diagnostic labels."""

    UNLIMITED = None

    max_calls: int | None
    max_tokens: int | None
    calls_label: str
    tokens_label: str

    @property
    def active(self):
        return self.max_calls is not self.UNLIMITED or self.max_tokens is not self.UNLIMITED


@dataclass(frozen=True, slots=True)
class UsagePolicy:
    """Read-only effective quota view; it never materializes omitted settings."""

    INVALID_COUNTER: ClassVar[str] = "Usage counter must be a nonempty name"
    counter: str | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.DESCRIPTION: "누적 토큰 예약 계산기 이름. Engine 입력 선택과 독립적이다", SchemaKeyword.MIN_LENGTH: 1}})
    max_calls: int | None = policy_field(default=UsageLimit.UNLIMITED, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "Run의 누적 모델 호출 상한", SchemaKeyword.MINIMUM: 1}})
    max_tokens: int | None = policy_field(default=UsageLimit.UNLIMITED, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "Run 토큰 예약 상한", SchemaKeyword.MINIMUM: 1}})
    project_max_calls: int | None = policy_field(default=UsageLimit.UNLIMITED, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "Project 기간 내 모델 호출 상한", SchemaKeyword.MINIMUM: 1}})
    project_max_tokens: int | None = policy_field(default=UsageLimit.UNLIMITED, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "Project 기간 내 토큰 예약 상한", SchemaKeyword.MINIMUM: 1}})
    period_seconds: float | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "Project 사용량 집계 기간; null은 전체", SchemaKeyword.EXCLUSIVE_MINIMUM: 0}})

    @classmethod
    def schema(cls, *, counters=()):
        value = _declared_policy_schema(cls)
        if counters:
            value[SchemaKeyword.PROPERTIES][cls.counter.__name__][SchemaKeyword.ENUM] = list(counters)
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
        return self.max_tokens is not UsageLimit.UNLIMITED or self.project_max_tokens is not UsageLimit.UNLIMITED

    def without_run_limits(self):
        """Unbound Component calls retain Project limits and have no owning Run scope."""
        return replace(self, max_calls=UsageLimit.UNLIMITED, max_tokens=UsageLimit.UNLIMITED)


@dataclass(frozen=True, slots=True)
class RunPolicy(PolicyValue):
    """Explicit Session queue and whole-Run limits; None preserves absence."""

    SECTION = PolicySection.RUN

    max_queued: int | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.MINIMUM: 1, SchemaKeyword.DESCRIPTION: "Session별 대기 요청 수 상한"}})
    timeout_seconds: float | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL], SchemaKeyword.EXCLUSIVE_MINIMUM: 0, SchemaKeyword.DESCRIPTION: "문맥 준비 포함 Run 기한(초)"}})
    max_capability_rounds: int | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.MINIMUM: 1, SchemaKeyword.DESCRIPTION: "Engine capability 확장 탐색 횟수"}})

    @classmethod
    def schema(cls):
        return _declared_policy_schema(cls)

    def __post_init__(self):
        _validate_runtime_policy(self)


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Conversation selection choices, shared by Schema and runtime consumers."""

    MISSING_BUDGET: ClassVar[str] = "Context budget mode requires max_chars"
    MISSING_TURNS: ClassVar[str] = "Recent context requires explicit max_turns"
    UNSPECIFIED: ClassVar[None] = None
    COMPLETED_MODES: ClassVar[tuple] = (ContextMode.COMPLETED.value, ContextMode.RECENT_COMPLETED.value)
    RECENT_MODES: ClassVar[tuple] = (ContextMode.RECENT.value, ContextMode.RECENT_COMPLETED.value)

    mode: str | None = policy_field(default=UNSPECIFIED, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.ENUM: [mode.value for mode in ContextMode],
        SchemaKeyword.DESCRIPTION: "엔진에 전달할 과거 대화 선택 방식"}})
    max_turns: int | None = policy_field(default=UNSPECIFIED, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.INTEGER, SchemaKeyword.MINIMUM: 1, SchemaKeyword.DESCRIPTION: "recent 계열의 과거 턴 수; 현재 입력은 별도"}})
    max_chars: int | None = policy_field(default=UNSPECIFIED, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.MINIMUM: 1, SchemaKeyword.DESCRIPTION: "현재 입력 포함 대화 본문 문자 상한"}})

    @classmethod
    def schema(cls):
        return _declared_policy_schema(cls)

    @staticmethod
    def validate_budget(mode, max_chars):
        if mode == ContextMode.BUDGET and max_chars is ContextPolicy.UNSPECIFIED:
            raise ValueError(ContextPolicy.MISSING_BUDGET)

    @classmethod
    def selected(cls, policies):
        value = policies.get(PolicySection.CONTEXT)
        return cls(**deepcopy(value)) if value else cls.UNSPECIFIED

    @classmethod
    def completed_only(cls, policy):
        return policy.mode in cls.COMPLETED_MODES

    @classmethod
    def recent_only(cls, policy):
        return policy.mode in cls.RECENT_MODES

    @classmethod
    def has_character_limit(cls, policy):
        return policy.max_chars is not cls.UNSPECIFIED

    def __post_init__(self):
        _validate_runtime_policy(self)
        if ContextPolicy.recent_only(self) and self.max_turns is self.UNSPECIFIED:
            raise ValueError(self.MISSING_TURNS)
        self.validate_budget(self.mode, self.max_chars)


@dataclass(frozen=True)
class OutputBuffer:
    """Technical batching choices; a batch of one persists immediately."""

    IMMEDIATE_BATCH_SIZE: ClassVar[int] = 1
    batch_size: int = policy_field(default=IMMEDIATE_BATCH_SIZE, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.MINIMUM: IMMEDIATE_BATCH_SIZE,
        SchemaKeyword.DESCRIPTION: "델타 저장 묶음 크기; null은 백엔드 기본값"}})
    max_delay: float = policy_field(default=0.025, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL], SchemaKeyword.EXCLUSIVE_MINIMUM: 0,
        SchemaKeyword.DESCRIPTION: "델타 저장 최대 대기(초); null은 백엔드 기본값"}})
    max_chars: int = policy_field(default=65536, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.MINIMUM: 1,
        SchemaKeyword.DESCRIPTION: "델타 저장 묶음 문자 상한; null은 백엔드 기본값"}})

    @classmethod
    def schema(cls):
        return _declared_policy_schema(cls)

    def __post_init__(self):
        _validate_runtime_policy(self, allow_absent=PolicyDeclaration.REQUIRE_VALUES)

    @property
    def immediate(self):
        return self.batch_size == self.IMMEDIATE_BATCH_SIZE

    def for_project(self, policy):
        """Stored null keeps the explicitly selected host batching value."""
        return type(self)(**{item.name: policy[item.name] if policy.get(item.name) is not None
                            else getattr(self, item.name) for item in fields(self)})

    def for_policies(self, policies):
        return self.for_project(policies.get(PolicySection.OUTPUT, {}))



class ConstraintMode(StrEnum):
    FIXED = "fixed"
    SELECTABLE = "selectable"
    BOUNDED = "bounded"


class ArgumentConstraint:
    """Project-owned rule syntax and narrowing; Tool schemas remain independent."""

    MODE = "mode"
    VALUE = "value"
    VALUES = "values"
    BOUNDS = (SchemaKeyword.MINIMUM, SchemaKeyword.MAXIMUM)
    VALUE_SCHEMA_OWNER = "selected Tool parameter schema"
    MIN_CHOICES = 1
    INVALID = "Invalid Tool argument constraint"
    WIDENING = "Child Tool argument constraint widens its parent"
    REQUIRED_PARENT_MODE = "Child constraint must preserve a fixed/selectable parent"
    INVALID_MODE = False
    NO_CHOICES = ()

    def __init__(self, rule):
        from llm.core.json_data import JsonObject
        from .interactions import same_interaction_value
        JsonObject.validate(rule)
        if not isinstance(rule, dict):
            raise ValueError(self.INVALID)
        mode = rule.get(self.MODE)
        if mode == ConstraintMode.FIXED:
            valid = rule.keys() == {self.MODE, self.VALUE}
        elif mode == ConstraintMode.SELECTABLE:
            values = rule.get(self.VALUES)
            valid = rule.keys() == {self.MODE, self.VALUES} and isinstance(values, list) and bool(values)
            if valid:
                valid = all(not any(same_interaction_value(v, other) for other in values[:i])
                            for i, v in enumerate(values))
        elif mode == ConstraintMode.BOUNDED:
            bounds = {k: v for k, v in rule.items() if k != self.MODE}
            valid = bool(bounds) and not bounds.keys() - set(self.BOUNDS) and all(
                type(v) in (int, float) and math.isfinite(v) for v in bounds.values())
            if valid and bounds.keys() == set(self.BOUNDS):
                valid = bounds[SchemaKeyword.MINIMUM] <= bounds[SchemaKeyword.MAXIMUM]
        else:
            valid = self.INVALID_MODE
        if not valid:
            raise ValueError(self.INVALID)
        self._rule = deepcopy(rule)
        self.mode = ConstraintMode(mode)

    @property
    def choices(self):
        if self.mode == ConstraintMode.FIXED:
            return [deepcopy(self._rule[self.VALUE])]
        if self.mode == ConstraintMode.SELECTABLE:
            return deepcopy(self._rule[self.VALUES])
        return self.NO_CHOICES

    def value_schema(self):
        if self.mode == ConstraintMode.FIXED:
            return {SchemaKeyword.CONST: deepcopy(self._rule[self.VALUE])}
        if self.mode == ConstraintMode.SELECTABLE:
            return {SchemaKeyword.ENUM: deepcopy(self._rule[self.VALUES])}
        return {SchemaKeyword.TYPE: SchemaType.NUMBER, **{k: v for k, v in self._rule.items() if k != self.MODE}}

    def narrow(self, parent):
        result = deepcopy(self._rule)
        if self.mode in (ConstraintMode.FIXED, ConstraintMode.SELECTABLE):
            validator = Draft202012Validator(parent.value_schema())
            if not all(validator.is_valid(v) for v in self.choices):
                raise ValueError(self.WIDENING)
        elif parent.mode == ConstraintMode.BOUNDED:
            for bound in self.BOUNDS:
                if bound in parent._rule:
                    if bound not in result:
                        result[bound] = parent._rule[bound]
                    elif (bound == SchemaKeyword.MINIMUM and result[bound] < parent._rule[bound]
                          or bound == SchemaKeyword.MAXIMUM and result[bound] > parent._rule[bound]):
                        raise ValueError(self.WIDENING)
        else:
            raise ValueError(self.REQUIRED_PARENT_MODE)
        return ArgumentConstraint(result)

    def to_dict(self):
        return deepcopy(self._rule)

    @classmethod
    def schema(cls):
        value_schema = {SchemaExtension.OWNER: cls.VALUE_SCHEMA_OWNER, SchemaExtension.OPEN_KIND: OpenSchemaKind.DATA}
        rules = []
        for mode, properties, required in (
            (ConstraintMode.FIXED, {cls.VALUE: value_schema}, [cls.VALUE]),
            (ConstraintMode.SELECTABLE, {cls.VALUES: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.ITEMS: value_schema,
                SchemaKeyword.MIN_ITEMS: cls.MIN_CHOICES, SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT}}, [cls.VALUES]),
            (ConstraintMode.BOUNDED, {SchemaKeyword.MINIMUM: {SchemaKeyword.TYPE: SchemaType.NUMBER}, SchemaKeyword.MAXIMUM: {SchemaKeyword.TYPE: SchemaType.NUMBER}}, []),
        ):
            rule = {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
                    SchemaKeyword.PROPERTIES: {cls.MODE: {SchemaKeyword.CONST: mode.value}, **properties}, SchemaKeyword.REQUIRED: [cls.MODE, *required]}
            if mode == ConstraintMode.BOUNDED:
                rule[SchemaKeyword.ANY_OF] = [{SchemaKeyword.REQUIRED: [SchemaKeyword.MINIMUM]}, {SchemaKeyword.REQUIRED: [SchemaKeyword.MAXIMUM]}]
            rules.append(rule)
        return {SchemaKeyword.ONE_OF: rules}


class ToolConstraintMap:
    INVALID_OBJECT = "argument_constraints must be an object"
    INVALID_NAMES = "Tool constraints require named argument objects"
    MIN_NAME_LENGTH = 1


def validate_tool_constraints(constraints: dict) -> None:
    from llm.core.json_data import JsonObject
    JsonObject.validate(constraints)
    if not isinstance(constraints, dict):
        raise ValueError(ToolConstraintMap.INVALID_OBJECT)
    for name, fields in constraints.items():
        if not isinstance(name, str) or not name or not isinstance(fields, dict):
            raise ValueError(ToolConstraintMap.INVALID_NAMES)
        for key, rule in fields.items():
            if not isinstance(key, str) or not key:
                raise ValueError(ArgumentConstraint.INVALID)
            ArgumentConstraint(rule)


def tool_constraints_schema():
    return {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.PROPERTY_NAMES: {SchemaKeyword.MIN_LENGTH: ToolConstraintMap.MIN_NAME_LENGTH}, SchemaKeyword.ADDITIONAL_PROPERTIES: {
        SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.PROPERTY_NAMES: {SchemaKeyword.MIN_LENGTH: ToolConstraintMap.MIN_NAME_LENGTH}, SchemaKeyword.ADDITIONAL_PROPERTIES: ArgumentConstraint.schema()}}


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


@dataclass(frozen=True, slots=True)
class ApprovalRule:
    id: str = policy_field(metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: 1}})
    category: str = policy_field(metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: 1}})
    max_risk: int = policy_field(metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.INTEGER, SchemaKeyword.MINIMUM: 0}})

    @classmethod
    def schema(cls):
        value = _declared_policy_schema(cls)
        value[SchemaKeyword.REQUIRED] = [item.name for item in fields(cls)]
        return value


@dataclass(frozen=True, slots=True)
class ApprovalPolicy(PolicyValue):
    SECTION = PolicySection.APPROVAL
    DUPLICATE_RULE = "Approval rule IDs must be unique"
    REQUIRED_SCHEME = "Approval rules require risk_scheme"
    INVALID_RISK = "Approval max_risk must be a nonnegative integer"
    ABSENT_SCHEME = ""

    enabled: bool = policy_field(default=False, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.BOOLEAN, SchemaKeyword.DESCRIPTION: "프로젝트 자동 승인 규칙 적용"}})
    risk_scheme: str | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.DESCRIPTION: "Application이 소유한 위험도 척도 identity",
        SchemaKeyword.MIN_LENGTH: 1}})
    rules: list[ApprovalRule] = policy_field(default_factory=list, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.DESCRIPTION: "카테고리·위험도별 승인 규칙; 불확실한 재실행은 제외",
        SchemaKeyword.ITEMS: ApprovalRule.schema()}})

    @classmethod
    def validated(cls, value):
        """Project schema has already checked shape; preserve semantic error order."""
        rules = [ApprovalRule(**deepcopy(rule)) for rule in value.get(cls.rules.__name__, [])]
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError(cls.DUPLICATE_RULE)
        if rules and not str(value.get(cls.risk_scheme.__name__, cls.ABSENT_SCHEME)).strip():
            raise ValueError(cls.REQUIRED_SCHEME)
        if any(type(rule.max_risk) is not int for rule in rules):
            raise ValueError(cls.INVALID_RISK)
        values = deepcopy(value)
        values[cls.rules.__name__] = rules
        return cls(**values)

    @classmethod
    def from_dict(cls, value):
        values = normalize_policies({cls.SECTION: value})
        return cls.validated(values[cls.SECTION])


@dataclass(frozen=True, slots=True)
class ToolPolicyValues(PolicyValue):
    SECTION = PolicySection.TOOLS
    UNRESTRICTED = None

    allowed_tools: list[str] | None = policy_field(default=UNRESTRICTED, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.ARRAY, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "Project에서 허용할 Tool 이름",
        SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT,
        SchemaKeyword.ITEMS: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: 1}}})
    max_calls: int | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "Run 전체 Tool 호출 한도",
        SchemaKeyword.MINIMUM: 1}})
    timeout_seconds: float | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "각 Tool 호출 기한; Engine은 더 좁힐 수 있다",
        SchemaKeyword.EXCLUSIVE_MINIMUM: 0}})
    max_output_chars: int | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "각 Tool 결과 문자 한도",
        SchemaKeyword.MINIMUM: 1}})
    argument_constraints: dict = policy_field(default_factory=dict, metadata={PolicyDeclaration.SCHEMA: tool_constraints_schema()})


    @classmethod
    def permits_selection(cls, allowed_tools, selected):
        """One explicit parent Tool set governs preflight and child scope creation."""
        return allowed_tools is cls.UNRESTRICTED or set(selected).issubset(allowed_tools)


@dataclass(frozen=True, slots=True)
class ToolRetryPolicy(PolicyValue):
    SECTION = PolicySection.TOOL_RETRY

    max_retries: int | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "조건부 Tool 재시도; null은 비활성화",
        SchemaKeyword.MINIMUM: 0}})
    delay_seconds: float | None = policy_field(default=None, metadata={PolicyDeclaration.SCHEMA: {
        SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL], SchemaKeyword.DESCRIPTION: "Tool 재시도 대기; null은 비활성화",
        SchemaKeyword.MINIMUM: 0}})

    @classmethod
    def apply_defaults(cls, policies, *, max_retries, delay_seconds):
        retry = policies.setdefault(cls.SECTION, {})
        retry.setdefault(cls.max_retries.__name__, max_retries)
        retry.setdefault(cls.delay_seconds.__name__, delay_seconds)


def describe_policies() -> dict:
    """UI용 필드·설명을 포함한 독립 JSON Schema를 만든다."""
    return {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
        SchemaKeyword.NOT: {SchemaKeyword.ANY_OF: [{SchemaKeyword.REQUIRED: [name]} for name in PolicyDeclaration.REMOVED]},
        SchemaKeyword.PROPERTIES: {
            PolicySection.APPROVAL: ApprovalPolicy.schema(), PolicySection.TOOLS: ToolPolicyValues.schema(),
            PolicySection.CONTEXT: ContextPolicy.schema(), PolicySection.OUTPUT: OutputBuffer.schema(),
            PolicySection.TOOL_RETRY: ToolRetryPolicy.schema(), PolicySection.USAGE: UsagePolicy.schema(),
            PolicySection.RUN: RunPolicy.schema()}}


def normalize_policies(value: dict) -> dict:
    """명시된 키만 검증한다. 생략한 정책과 leaf는 그대로 생략한다."""
    schema = describe_policies()
    json.dumps(value, allow_nan=JsonObject.ALLOW_NON_FINITE)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        path = PolicyDeclaration.PATH_SEPARATOR.join(str(part) for part in error.path)
        raise ValueError(PolicyDeclaration.INVALID.format(path=path, message=error.message))
    result = deepcopy(value)
    validate_tool_constraints(result.get(PolicySection.TOOLS, {}).get(ToolPolicyValues.argument_constraints.__name__, {}))
    # 존재하는 키만 검증·보존한다. missing은 정책 활성화나 기본값 생성을 뜻하지 않는다.
    for name, section in schema[SchemaKeyword.PROPERTIES].items():
        for key, spec in section[SchemaKeyword.PROPERTIES].items():
            number = result.get(name, {}).get(key)
            kind = spec[SchemaKeyword.TYPE]
            if (kind == SchemaType.INTEGER or isinstance(kind, list) and SchemaType.INTEGER in kind):
                if number is not None and type(number) is not int:
                    raise ValueError(PolicyDeclaration.INTEGER_REQUIRED.format(name=name, key=key))
    ApprovalPolicy.validated(result.get(PolicySection.APPROVAL, {}))
    context = result.get(PolicySection.CONTEXT, {})
    ContextPolicy.validate_budget(context.get(ContextPolicy.mode.__name__), context.get(ContextPolicy.max_chars.__name__))
    UsagePolicy.from_policies(result)
    return result
