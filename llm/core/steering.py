"""실행 중 추가 지시의 조회 계약. Run 상태나 별도 영속 도메인을 만들지 않는다."""

from copy import deepcopy
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Optional

from .contracts import JsonValue
from .models import Message, MessageRole, MessageStatus
from llm.errors import CodedError


INSTRUCTION_INPUT_STATUS = "input"


class SteeringScope:
    ROOT = None


class InstructionDataError(ValueError, CodedError):
    """지시 저장 계약 위반. 누락된 대상을 추측하거나 옛 형식을 변환하지 않는다."""

    code = "invalid_instruction_data"
    MESSAGE = "Invalid instruction data: {location}; original records are not converted"

    def __init__(self, location: str):
        super().__init__(self.MESSAGE.format(location=location))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True, slots=True, init=False)
class InstructionCheckpoint:
    """The instruction payload owner, including its scope and original message IDs."""

    KIND = "instruction"
    INVALID_IDENTITY = "checkpoint kind/status/target_scope"
    INVALID_MESSAGES = "checkpoint message_ids"

    class Field(StrEnum):
        KIND = "kind"
        STATUS = "status"
        TARGET_SCOPE = "target_scope"
        MESSAGE_IDS = "message_ids"

    _value: dict

    @classmethod
    def is_record(cls, value):
        return isinstance(value, dict) and value.get(cls.Field.KIND) == cls.KIND

    @classmethod
    def create(cls, target_scope, message_ids):
        return cls.read({cls.Field.KIND: cls.KIND, cls.Field.STATUS: INSTRUCTION_INPUT_STATUS,
                         cls.Field.TARGET_SCOPE: target_scope, cls.Field.MESSAGE_IDS: list(message_ids)})

    @classmethod
    def read(cls, value):
        if (not cls.is_record(value) or value.get(cls.Field.STATUS) != INSTRUCTION_INPUT_STATUS
                or cls.Field.TARGET_SCOPE not in value
                or value[cls.Field.TARGET_SCOPE] is not SteeringScope.ROOT and not _text(value[cls.Field.TARGET_SCOPE])):
            raise InstructionDataError(cls.INVALID_IDENTITY)
        ids = value.get(cls.Field.MESSAGE_IDS)
        if not isinstance(ids, list) or any(not _text(v) for v in ids) or len(set(ids)) != len(ids):
            raise InstructionDataError(cls.INVALID_MESSAGES)
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        return result

    @property
    def target_scope(self):
        return self._value[self.Field.TARGET_SCOPE]

    @property
    def message_ids(self):
        return tuple(self._value[self.Field.MESSAGE_IDS])

    def to_dict(self):
        return deepcopy(self._value)

    @classmethod
    def replace_message_ids(cls, record, identifiers):
        """Return a new validated record, preserving the envelope and unrelated receipts."""
        from llm.core.checkpoints import CheckpointEnvelope, unwrap_record
        payload, _ = unwrap_record(record)
        value = cls.read(payload).to_dict()
        value[cls.Field.MESSAGE_IDS] = list(identifiers)
        updated = cls.read(value).to_dict()
        return CheckpointEnvelope.attach(record, updated) if CheckpointEnvelope.is_record(record) else updated


def validate_instruction_record(value):
    """Validate through the single payload Reader; arbitrary Engine payloads stay opaque."""
    InstructionCheckpoint.read(value)


class InstructionStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    UNAPPLIED = "unapplied"
    PARTIALLY_APPLIED = "partially_applied"


class SteeringMode(StrEnum):
    """상속 자체는 지원 선언이 아니다. 전달자는 직접 입력을 소비하지 않는다."""

    UNSUPPORTED = "unsupported"
    CONSUME = "consume"
    FORWARD = "forward"


@dataclass(frozen=True, slots=True)
class SteeringRoute(JsonValue):
    """Run 스냅샷의 예약 가능 경로. 반복 번호와 실행 ID를 포함하지 않는다.

    workflow_path는 Graph가 제공하는 구조화된 경로다. 표시 문자열을 분할해 만들지 않는다.
    같은 경로에서 접수 이후 시작되는 다음 실행 하나에만 예약을 결합한다.
    """

    INVALID_ROUTE = "reservation route"
    KEY_JSON = {"ensure_ascii": False, "separators": (",", ":")}

    workflow_path: tuple[str, ...]
    node_id: str
    engine: str

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.workflow_path or any(not _text(v) for v in (*self.workflow_path, self.node_id, self.engine)):
            raise InstructionDataError(self.INVALID_ROUTE)

    @property
    def key(self) -> str:
        return self.encode_path([*self.workflow_path, self.node_id])

    @classmethod
    def encode_path(cls, path):
        """Encode a structural path already selected by its execution Owner."""
        return json.dumps(path, **cls.KEY_JSON)


@dataclass(frozen=True, slots=True)
class SteeringTarget(JsonValue):
    """이번 Run의 Engine 실행. id는 재사용하지 않고 scope는 명시적 재개에 사용한다."""

    INVALID_STATE = "target identity/state"
    INVALID_DESCRIPTOR = "target descriptor"
    CLOSED: ClassVar[bool] = False

    id: str
    run_id: str
    engine: str
    mode: SteeringMode
    scope: Optional[str] = SteeringScope.ROOT
    node_id: Optional[str] = None
    node_path: Optional[str] = None
    accepting: bool = CLOSED
    reason: Optional[str] = None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if (any(not _text(v) for v in (self.id, self.run_id, self.engine))
                or any(v is not None and not _text(v) for v in (self.scope, self.node_id, self.node_path, self.reason))
                or self.accepting and (self.mode != SteeringMode.CONSUME or self.reason is not None)):
            raise InstructionDataError(self.INVALID_STATE)

    def registration(self):
        """Engine opening request preserves the existing omitted reason field."""
        value = self.to_dict()
        del value[type(self).reason.__name__]
        return value

    @classmethod
    def from_dict(cls, value: dict) -> "SteeringTarget":
        mode = cls.mode.__name__
        try:
            return cls(**{**value, mode: SteeringMode(value[mode])})
        except (KeyError, TypeError, ValueError) as error:
            raise InstructionDataError(cls.INVALID_DESCRIPTOR) from error


def is_instruction(message: Message) -> bool:
    from .instruction_records import InstructionMessageRecord
    return InstructionMessageRecord.present(message.metadata)


def is_queued_request(message: Message) -> bool:
    return (message.role == MessageRole.USER and message.status == MessageStatus.QUEUED
            and not is_instruction(message))


@dataclass(frozen=True, slots=True)
class RunInstruction(JsonValue):
    """applied는 모델 입력에 포함했다는 뜻이며 모델의 이행/성공을 보장하지 않는다."""

    id: str
    run_id: str
    content: str
    status: InstructionStatus
    created_at: str
    applications: list[dict] = field(default_factory=list)
    reason: Optional[str] = None
    targets: list[dict] = field(default_factory=list)

    @staticmethod
    def validate_message(message: Message) -> None:
        """Validate once through the Message receipt Owner without repairing evidence."""
        from .instruction_records import InstructionMessageRecord
        InstructionMessageRecord.from_message(message)

    @classmethod
    def from_message(cls, message: Message) -> "RunInstruction":
        from .instruction_records import InstructionMessageRecord
        value = InstructionMessageRecord.from_message(message)
        return cls(message.id, value.run_id, message.content, value.status, message.created_at,
                   value.application_records, value.reason, [target.to_dict() for target in value.targets])
