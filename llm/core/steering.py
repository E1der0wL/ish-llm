"""실행 중 추가 지시의 조회 계약. Run 상태나 별도 영속 도메인을 만들지 않는다."""

from copy import deepcopy
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

from .contracts import JsonValue
from .models import Message, MessageRole, MessageStatus
from llm.errors import CodedError


INSTRUCTION_INPUT_STATUS = "input"


class InstructionDataError(ValueError, CodedError):
    """지시 저장 계약 위반. 누락된 대상을 추측하거나 옛 형식을 변환하지 않는다."""

    code = "invalid_instruction_data"

    def __init__(self, location: str):
        super().__init__(f"Invalid instruction data: {location}; original records are not converted")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True, init=False)
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
                or value[cls.Field.TARGET_SCOPE] is not None and not _text(value[cls.Field.TARGET_SCOPE])):
            raise InstructionDataError(cls.INVALID_IDENTITY)
        ids = value.get(cls.Field.MESSAGE_IDS)
        if not isinstance(ids, list) or any(not _text(v) for v in ids) or len(set(ids)) != len(ids):
            raise InstructionDataError(cls.INVALID_MESSAGES)
        result = object.__new__(cls)
        object.__setattr__(result, "_value", deepcopy(value))
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

    workflow_path: tuple[str, ...]
    node_id: str
    engine: str

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.workflow_path or any(not _text(v) for v in (*self.workflow_path, self.node_id, self.engine)):
            raise InstructionDataError("reservation route")

    @property
    def key(self) -> str:
        return json.dumps([*self.workflow_path, self.node_id], ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class SteeringTarget(JsonValue):
    """이번 Run의 Engine 실행. id는 재사용하지 않고 scope는 명시적 재개에 사용한다."""

    id: str
    run_id: str
    engine: str
    mode: SteeringMode
    scope: Optional[str] = None
    node_id: Optional[str] = None
    node_path: Optional[str] = None
    accepting: bool = False
    reason: Optional[str] = None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if (any(not _text(v) for v in (self.id, self.run_id, self.engine))
                or any(v is not None and not _text(v) for v in (self.scope, self.node_id, self.node_path, self.reason))
                or self.accepting and (self.mode != SteeringMode.CONSUME or self.reason is not None)):
            raise InstructionDataError("target identity/state")

    @classmethod
    def from_dict(cls, value: dict) -> "SteeringTarget":
        try:
            return cls(**{**value, "mode": SteeringMode(value["mode"])})
        except (KeyError, TypeError, ValueError) as error:
            raise InstructionDataError("target descriptor") from error


def is_instruction(message: Message) -> bool:
    return "steering" in message.metadata


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
        """읽기 경계에서 현재 계약을 검사한다. 본문이나 대상을 복구·생성하지 않는다."""
        value = message.metadata.get("steering")
        if (message.role != MessageRole.USER or not isinstance(value, dict)
                or any(not _text(value.get(k)) for k in ("run_id", "input_message_id"))
                or message.run_id is not None and message.run_id != value["run_id"]):
            raise InstructionDataError("message ownership")
        targets = value.get("targets")
        if not isinstance(targets, list) or not targets:
            raise InstructionDataError("message targets (nonempty list required)")
        ids, scopes, bound_scopes, states, applications = set(), set(), set(), set(), []
        for target in targets:
            if (not isinstance(target, dict) or not _text(target.get("id")) or "scope" not in target
                    or target["scope"] is not None and not _text(target["scope"])
                    or target.get("status") not in (InstructionStatus.PENDING, InstructionStatus.APPLIED, InstructionStatus.UNAPPLIED)
                    or not isinstance(target.get("applications"), list)):
                raise InstructionDataError("message target fields")
            reserved = "reservation" in target
            if reserved:
                try:
                    route = SteeringRoute.from_dict(target["reservation"])
                except (TypeError, ValueError) as error:
                    raise InstructionDataError("message reservation route") from error
                if ("execution_id" not in target
                        or target["execution_id"] is not None and not _text(target["execution_id"])
                        or target["execution_id"] is not None and target["scope"] is None
                        or target["status"] == InstructionStatus.APPLIED and target["execution_id"] is None
                        or len(target["applications"]) != (1 if target["status"] == InstructionStatus.APPLIED else 0)):
                    raise InstructionDataError("reservation binding")
            identity = ("reservation", route.key) if reserved else ("execution", target["scope"])
            if target["id"] in ids or identity in scopes:
                raise InstructionDataError("duplicate message target identity/scope")
            if target["scope"] is not None:
                if target["scope"] in bound_scopes:
                    raise InstructionDataError("duplicate bound reservation scope")
                bound_scopes.add(target["scope"])
            ids.add(target["id"])
            scopes.add(identity)
            states.add(target["status"])
            if target["status"] == InstructionStatus.UNAPPLIED and not _text(target.get("reason")):
                raise InstructionDataError("unapplied target reason")
            for application in target["applications"]:
                if (not isinstance(application, dict)
                        or any(not _text(application.get(k)) for k in ("run_id", "boundary", "target_id", "time"))
                        or "step_id" not in application
                        or application["step_id"] is not None and not _text(application["step_id"])):
                    raise InstructionDataError("target application")
                if reserved and (application["target_id"] != target["execution_id"]
                                 or application["run_id"] != value["run_id"]):
                    raise InstructionDataError("reservation application ownership")
            if target["status"] == InstructionStatus.APPLIED and not target["applications"]:
                raise InstructionDataError("applied target requires application")
            applications.extend(target["applications"])
        status = (InstructionStatus.PENDING if InstructionStatus.PENDING in states else InstructionStatus.APPLIED if states == {InstructionStatus.APPLIED}
                  else InstructionStatus.UNAPPLIED if states == {InstructionStatus.UNAPPLIED} else InstructionStatus.PARTIALLY_APPLIED)
        expected = (MessageStatus.QUEUED if status == InstructionStatus.PENDING else MessageStatus.CANCELLED
                    if status == InstructionStatus.UNAPPLIED else MessageStatus.COMMITTED)
        if value.get("status") != status or message.status != expected:
            raise InstructionDataError("message aggregate status")
        if value.get("applications", []) != applications:
            raise InstructionDataError("message aggregate applications")
        if status == InstructionStatus.UNAPPLIED and not _text(value.get("reason")):
            raise InstructionDataError("unapplied message reason")

    @classmethod
    def from_message(cls, message: Message) -> "RunInstruction":
        cls.validate_message(message)
        value = message.metadata["steering"]
        return cls(message.id, value["run_id"], message.content,
                   InstructionStatus(value["status"]), message.created_at,
                   deepcopy(value.get("applications", [])), value.get("reason"),
                   deepcopy(value["targets"]))
