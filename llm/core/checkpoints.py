"""체크포인트 수명 공통 계약. 파일과 Engine/Component payload는 소유자가 관리한다."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Optional

from .contracts import Diagnostic, JsonValue
from .plans import CheckpointPlan
from llm.errors import CodedError


class CheckpointStatus(StrEnum):
    """공통 실행 기록의 수명. 외부 효과나 승인 상태를 나타내지 않는다."""

    STARTED = "started"
    COMPLETED = "completed"
    WAITING = "waiting"
    RESPONDED = "responded"
    NOT_APPLIED = "not_applied"


class CheckpointOperation(StrEnum):
    INITIALIZE = "initialize"
    RECORD = "record"


@dataclass(frozen=True, slots=True)
class CheckpointEnvelope:
    """Common nested identity and lifecycle; the child owns the opaque payload."""

    KIND_FIELD: ClassVar[str] = "node_type"
    KIND: ClassVar[str] = "engine_record"
    PAYLOAD_FIELD: ClassVar[str] = "payload"
    engine_scope: str
    checkpoint_name: str
    local_key: str
    status: str
    container: bool
    requires_retry: bool

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.engine_scope, self.checkpoint_name, self.local_key)):
            raise ValueError("Checkpoint envelope requires scope, name and local key")
        if (self.status not in (CheckpointStatus.STARTED, CheckpointStatus.COMPLETED, CheckpointStatus.WAITING)
                or type(self.container) is not bool or type(self.requires_retry) is not bool
                or self.requires_retry != (self.status == CheckpointStatus.STARTED and not self.container)):
            raise ValueError("Invalid checkpoint envelope lifecycle")

    def to_dict(self):
        return {self.KIND_FIELD: self.KIND, **asdict(self)}

    @classmethod
    def is_record(cls, record):
        return record.get(cls.KIND_FIELD) == cls.KIND

    @classmethod
    def belongs_to(cls, record, scope, name):
        return record.get("engine_scope") == scope and record.get("checkpoint_name") == name

    @classmethod
    def from_dict(cls, record):
        if not cls.is_record(record):
            raise ValueError("Expected a nested Engine checkpoint envelope")
        return cls(**{item.name: record.get(item.name) for item in fields(cls)})

    @classmethod
    def read(cls, record):
        envelope = cls.from_dict(record)
        payload = record.get(cls.PAYLOAD_FIELD)
        if not isinstance(payload, dict):
            raise ValueError("Checkpoint envelope requires an object payload")
        return envelope, payload

    @classmethod
    def attach(cls, envelope, payload):
        cls.from_dict(envelope)
        return {**deepcopy(envelope), cls.PAYLOAD_FIELD: deepcopy(payload)}


class CheckpointErrorCode(StrEnum):
    CONFLICT = "checkpoint_conflict"
    PROTECTED = "checkpoint_protected"
    CORRUPT = "checkpoint_corrupt"
    MISSING = "checkpoint_missing"
    DELETED = "checkpoint_deleted"
    CONFIGURATION = "checkpoint_configuration"


class CheckpointError(CodedError, ValueError):
    """Structured rejection; retains ValueError catch compatibility, never grants access."""

    def __init__(self, code, message, *, blockers=()):
        self.code, self.blockers = code, tuple(blockers)
        super().__init__(f"{code}: {message}")
        self.diagnostic = Diagnostic(code, str(self), details={
            "boundary": "checkpoint", "blockers": [item.to_dict() for item in self.blockers]})


def select_checkpoint(names, name=None):
    """Omission selects the sole stored checkpoint, never an Engine preset."""
    if name is None:
        if len(names) != 1:
            raise ValueError("Specify a checkpoint name unless exactly one checkpoint exists")
        name = names[0]
    # Explicit names are resolved by the repository. In particular, a Run that
    # failed before checkpoint creation must still report missing, not corrupt.
    if not isinstance(name, str) or not name:
        raise ValueError("Checkpoint name must be nonempty text")
    return name


def checkpoint_lifecycle(payload, lifecycle=None):
    """Public nested checkpoint lifecycle; private payloads may supply an explicit projection.

    Standard records use started/completed/waiting; responded and not_applied
    denote persisted no-replay observations. Unknown private states require
    lifecycle={status, container} on the event, never a parent-specific adapter.
    """
    if lifecycle is not None:
        if (not isinstance(lifecycle, dict) or lifecycle.keys() != {"status", "container"}
                or lifecycle["status"] not in (CheckpointStatus.STARTED, CheckpointStatus.COMPLETED, CheckpointStatus.WAITING)
                or type(lifecycle["container"]) is not bool):
            raise ValueError("Invalid explicit checkpoint lifecycle")
        return lifecycle["status"], lifecycle["container"]
    state = payload.get("status")
    if state not in tuple(CheckpointStatus):
        raise ValueError("Private checkpoint states require an explicit lifecycle projection")
    return (state if state in (CheckpointStatus.STARTED, CheckpointStatus.COMPLETED, CheckpointStatus.WAITING)
            else CheckpointStatus.COMPLETED,
            state != CheckpointStatus.STARTED or payload.get("container") is True)


def build_envelope(*, engine_scope, checkpoint_name, local_key, status, container, requires_retry):
    """Common nested-record envelope, independent of the orchestrating Engine.

    The selected child owns payload. The envelope only carries identity and
    lifecycle facts required by persistence, steering and approval bindings.
    Field names are the existing storage contract; no migration is performed.
    """
    return CheckpointEnvelope(engine_scope, checkpoint_name, local_key,
                              status, container, requires_retry).to_dict()


def unwrap_record(record):
    """Return one owner's payload and optional scope, not arbitrary nested JSON."""
    if not CheckpointEnvelope.is_record(record):
        return record, None
    envelope, payload = CheckpointEnvelope.read(record)
    return payload, envelope.engine_scope


@dataclass(frozen=True, slots=True)
class ResumeAbandonment(JsonValue):
    """재개 권한의 영구 포기. 실행 취소나 파일 삭제, 외부 효과 취소가 아니다."""
    abandoned_at: str
    reason: str
    request_id: Optional[str] = None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.abandoned_at or self.reason not in ("explicit", "conversation_deleted"):
            raise ValueError("Invalid resume abandonment record")
        if datetime.fromisoformat(self.abandoned_at).utcoffset() is None:
            raise ValueError("Abandonment time requires a timezone")
        if self.reason == "conversation_deleted" and not self.request_id:
            raise ValueError("Conversation abandonment requires request_id")
        if self.reason == "explicit" and self.request_id is not None:
            raise ValueError("Explicit abandonment has no conversation request")


def require_checkpoint_plan(plan: CheckpointPlan, expected_revision: str, *, action: str) -> None:
    """계획은 권한이 아니다. 소유자가 잠금 안에서 재계산한 계획에만 적용한다."""
    if action not in ("abandon", "delete"):
        raise ValueError("Unknown checkpoint action")
    if not expected_revision or expected_revision != plan.revision:
        raise CheckpointError(CheckpointErrorCode.CONFLICT, "review a fresh checkpoint plan")
    blockers = plan.abandon_blockers if action == "abandon" else plan.delete_blockers
    if blockers:
        raise CheckpointError(CheckpointErrorCode.PROTECTED, "; ".join(item.message for item in blockers), blockers=blockers)
