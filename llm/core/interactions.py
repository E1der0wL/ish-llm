"""UI와 실행기가 공유하는 사용자 요청·선택지·응답. 실행이나 파일 저장을 수행하지 않는다."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from typing import Any, ClassVar, Optional

from .models import new_id, now
from llm.core.json_data import JsonObject
from .schema import checked_schema


class InteractionJSON:
    """Validation envelopes and exact JSON comparison; not persisted request fields."""

    VALUE = "value"
    DECISION = "decision"
    LEFT = "left"
    RIGHT = "right"
    SORT_KEYS = True
    ALLOW_NAN = False

    @classmethod
    def validate_value(cls, value):
        JsonObject.validate({cls.VALUE: value})

    @classmethod
    def validate_decision(cls, value):
        JsonObject.validate({cls.DECISION: value})

    @classmethod
    def same(cls, left, right):
        JsonObject.validate({cls.LEFT: left, cls.RIGHT: right})
        return json.dumps(left, sort_keys=cls.SORT_KEYS, allow_nan=cls.ALLOW_NAN) == json.dumps(
            right, sort_keys=cls.SORT_KEYS, allow_nan=cls.ALLOW_NAN
        )


class InteractionSnapshot:
    INVALID_OBJECT = "{name} must be an object"
    INVALID_FIELDS = "{name} requires all current fields; missing={missing}, extra={extra}"
    INVALID_TIMESTAMP = "Interaction timestamps require timezone-aware text"


class InteractionRisk:
    UNKNOWN = None
    MINIMUM = 0
    INVALID_SCHEME = "risk_scheme must be nonempty text"
    INVALID_VALUE = "risk requires a nonnegative integer and risk_scheme"

    @classmethod
    def validate(cls, scheme, risk):
        if scheme is not cls.UNKNOWN and (not isinstance(scheme, str) or not scheme.strip()):
            raise ValueError(cls.INVALID_SCHEME)
        if risk is not cls.UNKNOWN and (type(risk) is not int or risk < cls.MINIMUM or scheme is cls.UNKNOWN):
            raise ValueError(cls.INVALID_VALUE)


@dataclass(frozen=True, init=False)
class InteractionBinding:
    """Typed view of the common checkpoint binding, preserving opaque extensions."""

    INVALID = "Interaction source/action/binding must be objects"
    SELECTED = True
    NOT_SELECTED = False
    NO_KEY = None
    VALUE_ATTRIBUTE = "_value"

    class Field(StrEnum):
        CHECKPOINT = "checkpoint"
        KEY = "key"
        INPUT_KEY = "input_key"
        SELECTION = "selection"
        TARGET = "target"

    _value: dict

    def __init__(self, value):
        if not isinstance(value, dict):
            raise TypeError(self.INVALID)
        JsonObject.validate(value)
        object.__setattr__(self, self.VALUE_ATTRIBUTE, deepcopy(value))

    @classmethod
    def for_checkpoint(cls, checkpoint, key, *, input_key, selection):
        value = {cls.Field.CHECKPOINT: checkpoint, cls.Field.KEY: key, cls.Field.INPUT_KEY: input_key}
        if selection:
            value[cls.Field.SELECTION] = cls.SELECTED
        return cls(value)

    @classmethod
    def for_retry(cls, checkpoint, key, target):
        return cls({cls.Field.CHECKPOINT: checkpoint, cls.Field.KEY: key, cls.Field.TARGET: target})

    @property
    def checkpoint(self):
        return self._value.get(self.Field.CHECKPOINT)

    @property
    def key(self):
        return self._value.get(self.Field.KEY)

    @property
    def required_key(self):
        return self._value[self.Field.KEY]

    @property
    def input_key(self):
        return self._value.get(self.Field.INPUT_KEY)

    @property
    def selection(self):
        return self._value.get(self.Field.SELECTION) is self.SELECTED

    @property
    def target(self):
        return self._value.get(self.Field.TARGET)

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, slots=True)
class InteractionSelection:
    """Explicit selection wire value, distinct from the selected action's value."""

    INVALID: ClassVar[str] = "Interaction selection requires option_id and value"
    option_id: str
    value: Any

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID)
        return cls(**deepcopy(value))

    def to_dict(self):
        return asdict(self)


class InteractionStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class InteractionActor(StrEnum):
    USER = "user"
    POLICY = "policy"
    HOST = "host"


def _stored_fields(cls, value):
    """Creation defaults never repair a persisted interaction snapshot."""
    if not isinstance(value, dict):
        raise TypeError(InteractionSnapshot.INVALID_OBJECT.format(name=cls.__name__))
    names = {item.name for item in fields(cls)}
    if value.keys() != names:
        raise ValueError(
            InteractionSnapshot.INVALID_FIELDS.format(
                name=cls.__name__, missing=sorted(names - value.keys()), extra=sorted(value.keys() - names)
            )
        )
    JsonObject.validate(value)
    return deepcopy(value)


def _timestamp(value):
    if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is None:
        raise ValueError(InteractionSnapshot.INVALID_TIMESTAMP)


def same_interaction_value(left: Any, right: Any) -> bool:
    """JSON 응답을 강제 변환 없이 비교한다. 중첩된 true/1과 1/1.0도 구분한다."""
    return InteractionJSON.same(left, right)


@dataclass(frozen=True, slots=True)
class InteractionOption:
    """표시용 선택지와 엔진에 전달할 JSON 값. effect는 승인/거절 등의 의미를 나타낸다."""

    INVALID_IDENTITY: ClassVar[str] = "Interaction options require an ID and label"
    INVALID_TEXT: ClassVar[str] = "Invalid interaction option text"

    # Common effects have one declaration; application-defined effects remain open text.
    DEFAULT_APPROVAL_ID: ClassVar[str] = "approve"
    DEFAULT_DENIAL_ID: ClassVar[str] = "deny"
    APPROVAL_LABEL: ClassVar[str] = "승인"
    DENIAL_LABEL: ClassVar[str] = "거절"
    APPROVED: ClassVar[bool] = True
    DENIED: ClassVar[bool] = False
    EFFECT_SELECT: ClassVar[str] = "select"
    EFFECT_APPROVE: ClassVar[str] = "approve"
    EFFECT_DENY: ClassVar[str] = "deny"

    id: str
    label: str
    description: str = ""
    effect: str = EFFECT_SELECT
    value: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not isinstance(self.label, str) or not self.label:
            raise ValueError(InteractionOption.INVALID_IDENTITY)
        if not isinstance(self.description, str) or not isinstance(self.effect, str):
            raise TypeError(InteractionOption.INVALID_TEXT)
        InteractionJSON.validate_value(self.value)

    def to_dict(self) -> dict:
        value = asdict(self)
        JsonObject.validate(value)
        return deepcopy(value)

    @classmethod
    def from_dict(cls, value: dict) -> "InteractionOption":
        return cls(**_stored_fields(cls, value))


@dataclass(frozen=True, slots=True)
class InteractionRequest:
    """실행 대상과 UI 선택지를 담은 스냅샷. 추천 선택지는 자동 승인 권한이 아니다."""

    INVALID_REVISION: ClassVar[str] = "Interaction revision must be positive"
    INVALID_TEXT: ClassVar[str] = "Interaction title/description must be text"
    INVALID_KIND_STATUS: ClassVar[str] = "Invalid interaction kind/status"
    INVALID_PRIORITY: ClassVar[str] = "Invalid interaction priority"
    MISSING_CATEGORY: ClassVar[str] = "Interaction category is required"
    INVALID_OPTIONS: ClassVar[str] = "Interaction options must be a nonempty tuple"
    INVALID_SELECTION: ClassVar[str] = "Invalid or duplicate interaction option selection"
    INVALID_INPUT_SCHEMA: ClassVar[str] = "Interaction input_schema must be an object or null"
    INVALID_ID: ClassVar[str] = "Invalid interaction ID"
    INVALID_FINGERPRINT: ClassVar[str] = "Interaction fingerprint changed or is invalid"
    INVALID_STORED_OPTIONS: ClassVar[str] = "Stored interaction options must be an array"
    UNKNOWN_OPTION: ClassVar[str] = "Unknown interaction option"
    DENIAL_WITH_INPUT: ClassVar[str] = "Denial cannot include input"
    INPUT_NOT_ACCEPTED: ClassVar[str] = "Interaction does not accept input"
    INPUT_REPLACES_DECISION: ClassVar[str] = "Interaction input cannot replace the selected decision"
    UNMATCHED_DECISION: ClassVar[str] = (
        "Decision is not an interaction option; cannot edit approval arguments"
    )
    AMBIGUOUS_DECISION: ClassVar[str] = "Ambiguous interaction decision; select an option by ID"

    INITIAL_REVISION: ClassVar[int] = 1
    REVISION_STEP: ClassVar[int] = 1
    SINGLE_OPTION: ClassVar[int] = 1
    HEX_DIGITS: ClassVar[str] = "0123456789abcdef"
    FINGERPRINT_ASCII: ClassVar[bool] = False
    FINGERPRINT_SORT_KEYS: ClassVar[bool] = True
    INVALID_INPUT: ClassVar[str] = "Invalid interaction input: {message}"
    NO_SELECTION: ClassVar[None] = None
    NO_INPUT_SCHEMA: ClassVar[None] = None
    NO_EXPIRY: ClassVar[None] = None
    NO_INPUT: ClassVar[None] = None
    NO_OPTION: ClassVar[None] = None
    NO_CONTEXT: ClassVar[None] = None
    EMPTY_DESCRIPTION: ClassVar[str] = ""
    ALLOW_EMPTY_CONFIRMATION: ClassVar[bool] = True
    DISALLOW_EMPTY_CONFIRMATION: ClassVar[bool] = False
    ID_LENGTH: ClassVar[int] = 32
    FINGERPRINT_LENGTH: ClassVar[int] = hashlib.sha256().digest_size * 2
    KIND_APPROVAL: ClassVar[str] = "approval"
    KIND_CONFIRMATION: ClassVar[str] = "confirmation"
    KIND_CHOICE: ClassVar[str] = "choice"
    KIND_INPUT: ClassVar[str] = "input"
    KINDS: ClassVar[tuple[str, ...]] = (KIND_APPROVAL, KIND_CONFIRMATION, KIND_CHOICE, KIND_INPUT)
    PRIORITY_LOW: ClassVar[str] = "low"
    PRIORITY_NORMAL: ClassVar[str] = "normal"
    PRIORITY_HIGH: ClassVar[str] = "high"
    PRIORITY_URGENT: ClassVar[str] = "urgent"
    PRIORITIES: ClassVar[tuple[str, ...]] = (PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH, PRIORITY_URGENT)
    DEFAULT_CATEGORY: ClassVar[str] = "tool.execute"

    title: str
    options: tuple[InteractionOption, ...]
    id: str = field(default_factory=new_id)
    revision: int = INITIAL_REVISION
    kind: str = KIND_APPROVAL
    category: str = DEFAULT_CATEGORY
    priority: str = PRIORITY_NORMAL
    risk: Optional[int] = InteractionRisk.UNKNOWN
    risk_scheme: Optional[str] = InteractionRisk.UNKNOWN
    description: str = EMPTY_DESCRIPTION
    recommended_option_id: Optional[str] = NO_SELECTION
    input_schema: Optional[dict] = NO_INPUT_SCHEMA
    source: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)
    binding: dict = field(default_factory=dict)
    status: str = InteractionStatus.PENDING
    created_at: str = field(default_factory=now)
    expires_at: Optional[str] = NO_EXPIRY

    def __post_init__(self) -> None:
        self.validate_identity(self.id)
        if type(self.revision) is not int or self.revision < self.INITIAL_REVISION:
            raise ValueError(InteractionRequest.INVALID_REVISION)
        if not isinstance(self.title, str) or not self.title or not isinstance(self.description, str):
            raise ValueError(InteractionRequest.INVALID_TEXT)
        if self.kind not in InteractionRequest.KINDS or self.status not in InteractionStatus:
            raise ValueError(InteractionRequest.INVALID_KIND_STATUS)
        if self.priority not in InteractionRequest.PRIORITIES:
            raise ValueError(InteractionRequest.INVALID_PRIORITY)
        validate_risk(self.risk_scheme, self.risk)
        if not isinstance(self.category, str) or not self.category:
            raise ValueError(InteractionRequest.MISSING_CATEGORY)
        if (
            not isinstance(self.options, tuple)
            or not self.options
            or any(not isinstance(o, InteractionOption) for o in self.options)
        ):
            raise TypeError(InteractionRequest.INVALID_OPTIONS)
        ids = [o.id for o in self.options]
        if (
            len(ids) != len(set(ids))
            or self.recommended_option_id is not self.NO_SELECTION
            and self.recommended_option_id not in ids
        ):
            raise ValueError(InteractionRequest.INVALID_SELECTION)
        for value in (self.source, self.action, self.binding):
            if not isinstance(value, dict):
                raise TypeError(InteractionBinding.INVALID)
            JsonObject.validate(value)
        _timestamp(self.created_at)
        if self.expires_at is not self.NO_EXPIRY:
            _timestamp(self.expires_at)
        if self.input_schema is not self.NO_INPUT_SCHEMA:
            if not isinstance(self.input_schema, dict):
                raise TypeError(InteractionRequest.INVALID_INPUT_SCHEMA)
            checked_schema(self.input_schema)

    @classmethod
    def validate_identity(cls, identifier):
        if (
            not isinstance(identifier, str)
            or len(identifier) != cls.ID_LENGTH
            or any(c not in cls.HEX_DIGITS for c in identifier)
        ):
            raise ValueError(InteractionRequest.INVALID_ID)

    @classmethod
    def validate_fingerprint(cls, value):
        if (
            not isinstance(value, str)
            or len(value) != cls.FINGERPRINT_LENGTH
            or any(c not in cls.HEX_DIGITS for c in value)
        ):
            raise ValueError(InteractionRequest.INVALID_FINGERPRINT)

    @property
    def expired(self) -> bool:
        return self.expires_at is not self.NO_EXPIRY and datetime.fromisoformat(
            self.expires_at
        ) <= datetime.now(timezone.utc)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.to_dict(), ensure_ascii=self.FINGERPRINT_ASCII, sort_keys=self.FINGERPRINT_SORT_KEYS
            ).encode()
        ).hexdigest()

    @property
    def checkpoint_binding(self):
        return InteractionBinding(self.binding)

    def to_dict(self) -> dict:
        value = asdict(self)
        value[type(self).options.__name__] = [asdict(o) for o in self.options]
        JsonObject.validate(value)
        return deepcopy(value)

    @classmethod
    def from_dict(cls, value: dict):
        data = _stored_fields(cls, value)
        if not isinstance(data[cls.options.__name__], list):
            raise TypeError(InteractionRequest.INVALID_STORED_OPTIONS)
        data[cls.options.__name__] = tuple(InteractionOption.from_dict(o) for o in data[cls.options.__name__])
        return cls(**data)

    def bind(
        self,
        checkpoint: str,
        key: str,
        *,
        decision_key: Optional[str] = InteractionBinding.NO_KEY,
        input_key: Optional[str] = InteractionBinding.NO_KEY,
        selection: bool = InteractionBinding.NOT_SELECTED,
    ) -> "InteractionRequest":
        """Engine의 재개 형식은 선택지 값으로 표현한다. UI는 이 값을 해석할 필요가 없다."""
        options = tuple(
            replace(o, value={decision_key: o.value}) if decision_key and type(o.value) is bool else o
            for o in self.options
        )
        return replace(
            self,
            options=options,
            binding=InteractionBinding.for_checkpoint(
                checkpoint, key, input_key=input_key, selection=selection
            ).to_dict(),
        )

    def decision_for(self, option_id: str, *, value: Any = NO_INPUT) -> Any:
        """선택지와 입력의 순수 형식 변환. 요청 수명/응답 지문 검증이나 승인을 대신하지 않는다."""
        from jsonschema import Draft202012Validator, ValidationError

        InteractionJSON.validate_value(value)
        option = next((o for o in self.options if o.id == option_id), self.NO_OPTION)
        if option is self.NO_OPTION:
            raise ValueError(InteractionRequest.UNKNOWN_OPTION)
        decision = deepcopy(option.value)
        if option.effect == InteractionOption.EFFECT_DENY and value is not self.NO_INPUT:
            raise ValueError(InteractionRequest.DENIAL_WITH_INPUT)
        if option.effect != InteractionOption.EFFECT_DENY and self.input_schema is not self.NO_INPUT_SCHEMA:
            try:
                Draft202012Validator(self.input_schema).validate({} if value is self.NO_INPUT else value)
            except ValidationError as error:
                raise ValueError(self.INVALID_INPUT.format(message=error.message)) from error
        if value is not self.NO_INPUT:
            if self.input_schema is self.NO_INPUT_SCHEMA:
                raise ValueError(InteractionRequest.INPUT_NOT_ACCEPTED)
            if self.checkpoint_binding.selection:
                return InteractionSelection(option.id, value).to_dict()
            key = self.checkpoint_binding.input_key
            if key:
                if not isinstance(decision, dict) or key in decision:
                    raise ValueError(InteractionRequest.INPUT_REPLACES_DECISION)
                decision[key] = deepcopy(value)
            else:
                decision = deepcopy(value)
        return (
            InteractionSelection(option.id, self.NO_INPUT).to_dict()
            if self.checkpoint_binding.selection
            else decision
        )

    def select_decision(
        self, decision: Any, *, confirm_empty: bool = DISALLOW_EMPTY_CONFIRMATION
    ) -> tuple[InteractionOption, Any]:
        """재개 값을 유일한 선택지와 추가 입력으로 분리한다. 누락 응답은 호출자가 구분한다.

        빈 confirmation은 명시적 확인 경로에서만 승인 선택으로 해석한다. 일반 approval,
        choice에는 적용하지 않는다. 반환값은 사본이며 응답 영수증을 생성하지 않는다.
        """
        InteractionJSON.validate_decision(decision)
        if self.checkpoint_binding.selection:
            selected = InteractionSelection.from_dict(decision)
            self.decision_for(selected.option_id, value=selected.value)
            return deepcopy(next(o for o in self.options if o.id == selected.option_id)), deepcopy(
                selected.value
            )
        value = deepcopy(decision)
        input_key = self.checkpoint_binding.input_key
        supplied = (
            value.pop(input_key, self.NO_INPUT) if input_key and isinstance(value, dict) else self.NO_INPUT
        )
        if confirm_empty and self.kind == InteractionRequest.KIND_CONFIRMATION and value == {}:
            options = [o for o in self.options if o.effect == InteractionOption.EFFECT_APPROVE]
        else:
            options = [o for o in self.options if same_interaction_value(o.value, value)]
        if not options:
            raise ValueError(InteractionRequest.UNMATCHED_DECISION)
        if len(options) != self.SINGLE_OPTION:
            raise ValueError(InteractionRequest.AMBIGUOUS_DECISION)
        option = options[0]
        self.decision_for(option.id, value=supplied)
        return deepcopy(option), supplied

    def find_approval(self) -> Optional[InteractionOption]:
        """Only an unambiguous approval can be selected without a user's choice.

        Option IDs are presentation identities, not execution semantics. Required
        input belongs to the caller; no input or choice may be invented here.
        """
        options = [option for option in self.options if option.effect == InteractionOption.EFFECT_APPROVE]
        if len(options) != self.SINGLE_OPTION:
            return self.NO_OPTION
        try:
            self.decision_for(options[0].id)
        except ValueError:
            return self.NO_OPTION
        return deepcopy(options[0])

    @classmethod
    def renewal_revision(cls, revision):
        return revision + cls.REVISION_STEP

    def renewal(self, identifier, *, created_at, expires_at):
        """Pure successor snapshot; caller must establish cancellation or expiry evidence."""
        return replace(
            self,
            id=identifier,
            revision=self.renewal_revision(self.revision),
            created_at=created_at,
            expires_at=expires_at,
            status=InteractionStatus.PENDING,
        )

    def respond(self, option_id: str, *, value: Any = NO_INPUT) -> "InteractionResponse":
        """UI가 본 요청 버전과 실행 대상에 묶인 응답을 만든다. 저장/실행은 하지 않는다."""
        response = InteractionResponse(self.id, self.revision, option_id, self.fingerprint, value)
        response.decision(self)
        return response


@dataclass(frozen=True, slots=True)
class InteractionResponse:
    """사용자가 확인한 요청 버전/지문에 묶인 응답. 저장과 실행은 서비스가 담당한다."""

    INVALID_REVISION: ClassVar[str] = "Invalid interaction response revision"
    INVALID_IDENTITY: ClassVar[str] = "Invalid interaction response identity"
    INVALID_ACTOR: ClassVar[str] = "Invalid interaction response actor"
    INVALID_POLICY: ClassVar[str] = "Invalid interaction policy ID"
    MISSING_POLICY: ClassVar[str] = "Policy responses require their policy ID"
    CHANGED_REQUEST: ClassVar[str] = "Interaction changed; reload before responding"
    NOT_PENDING: ClassVar[str] = "Interaction is no longer pending"

    NO_POLICY: ClassVar[None] = None

    request_id: str
    request_revision: int
    option_id: str
    request_fingerprint: str
    value: Any = None
    actor: str = InteractionActor.USER
    created_at: str = field(default_factory=now)
    policy_id: Optional[str] = NO_POLICY

    def __post_init__(self) -> None:
        InteractionRequest.validate_identity(self.request_id)
        InteractionRequest.validate_fingerprint(self.request_fingerprint)
        if (
            type(self.request_revision) is not int
            or self.request_revision < InteractionRequest.INITIAL_REVISION
        ):
            raise ValueError(InteractionResponse.INVALID_REVISION)
        if any(
            not isinstance(v, str) or not v
            for v in (self.request_id, self.option_id, self.request_fingerprint)
        ):
            raise ValueError(InteractionResponse.INVALID_IDENTITY)
        if self.actor not in InteractionActor:
            raise ValueError(InteractionResponse.INVALID_ACTOR)
        _timestamp(self.created_at)
        InteractionJSON.validate_value(self.value)
        if self.policy_id is not self.NO_POLICY and (
            not isinstance(self.policy_id, str) or not self.policy_id
        ):
            raise ValueError(InteractionResponse.INVALID_POLICY)
        if (self.actor == InteractionActor.POLICY) != (self.policy_id is not self.NO_POLICY):
            raise ValueError(InteractionResponse.MISSING_POLICY)

    @classmethod
    def from_dict(cls, value: dict):
        return cls(**_stored_fields(cls, value))

    def to_dict(self) -> dict:
        value = asdict(self)
        JsonObject.validate(value)
        return deepcopy(value)

    def comparable(self):
        value = self.to_dict()
        value.pop(InteractionResponse.created_at.__name__)
        return value

    def decision(self, request: InteractionRequest) -> Any:
        if (
            self.request_id != request.id
            or self.request_revision != request.revision
            or self.request_fingerprint != request.fingerprint
        ):
            raise ValueError(InteractionResponse.CHANGED_REQUEST)
        if request.expired or request.status != InteractionStatus.PENDING:
            raise ValueError(InteractionResponse.NOT_PENDING)
        self.to_dict()
        if self.actor not in InteractionActor:
            raise ValueError(InteractionResponse.INVALID_ACTOR)
        return request.decision_for(self.option_id, value=self.value)


def validate_risk(scheme, risk):
    """Application의 순서 있는 척도만 비교한다. 미분류는 자동 승인 근거가 아니다."""
    InteractionRisk.validate(scheme, risk)


def approval_request(
    title: str,
    *,
    description: str = InteractionRequest.EMPTY_DESCRIPTION,
    source: Optional[dict] = InteractionRequest.NO_CONTEXT,
    action: Optional[dict] = InteractionRequest.NO_CONTEXT,
    category: str = InteractionRequest.DEFAULT_CATEGORY,
    risk: Optional[int] = InteractionRisk.UNKNOWN,
    risk_scheme: Optional[str] = InteractionRisk.UNKNOWN,
) -> InteractionRequest:
    return InteractionRequest(
        title,
        (
            InteractionOption(
                InteractionOption.DEFAULT_APPROVAL_ID,
                InteractionOption.APPROVAL_LABEL,
                effect=InteractionOption.EFFECT_APPROVE,
                value=InteractionOption.APPROVED,
            ),
            InteractionOption(
                InteractionOption.DEFAULT_DENIAL_ID,
                InteractionOption.DENIAL_LABEL,
                effect=InteractionOption.EFFECT_DENY,
                value=InteractionOption.DENIED,
            ),
        ),
        description=description,
        source={} if source is InteractionRequest.NO_CONTEXT else source,
        action={} if action is InteractionRequest.NO_CONTEXT else action,
        category=category,
        risk=risk,
        risk_scheme=risk_scheme,
    )


@dataclass(frozen=True, slots=True)
class InteractionView:
    """불변 요청과 현재 처리 상태의 투영. 사용자 결정과 실제 실행 결과를 구분한다."""

    NO_RESPONSE: ClassVar[None] = None
    NO_BLOCK_REASON: ClassVar[None] = None
    RESUME_ABANDONED: ClassVar[str] = "resume_abandoned"
    ALREADY_RESUMED: ClassVar[str] = "already_resumed"
    RUN_ACTIVE_OR_FINISHED: ClassVar[str] = "run_active_or_finished"
    REQUEST_CANCELLED: ClassVar[str] = "request_cancelled"
    REQUEST_EXPIRED: ClassVar[str] = "request_expired"

    PENDING: ClassVar[str] = "pending"
    ANSWERED: ClassVar[str] = "answered"
    DENIED: ClassVar[str] = "denied"
    EXPIRED: ClassVar[str] = "expired"
    CANCELLED: ClassVar[str] = "cancelled"
    SUBMITTED: ClassVar[str] = "submitted"
    UNAVAILABLE: ClassVar[str] = "unavailable"

    request: InteractionRequest
    response: Optional[InteractionResponse]
    status: str
    can_respond: bool
    can_resume: bool
    blocked_reason: Optional[str] = None
    resumed_run_id: Optional[str] = None
    execution_status: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            type(self).request.__name__: self.request.to_dict(),
            type(self).response.__name__: self.response.to_dict() if self.response else self.NO_RESPONSE,
        }
