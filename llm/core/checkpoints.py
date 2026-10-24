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


class CheckpointAction(StrEnum):
    ABANDON = "abandon"
    DELETE = "delete"


class ResumeAbandonmentReason(StrEnum):
    EXPLICIT = "explicit"
    CONVERSATION_DELETED = "conversation_deleted"


@dataclass(frozen=True, slots=True)
class CheckpointLifecycle:
    """Public lifecycle projection; private Engine states remain Engine-owned."""

    STATES: ClassVar[tuple] = (CheckpointStatus.STARTED, CheckpointStatus.COMPLETED, CheckpointStatus.WAITING)
    CONTAINER: ClassVar[bool] = True
    INVALID: ClassVar[str] = "Invalid explicit checkpoint lifecycle"
    PRIVATE_STATE: ClassVar[str] = "Private checkpoint states require an explicit lifecycle projection"
    status: str
    container: bool

    def __post_init__(self):
        if self.status not in self.STATES or type(self.container) is not bool:
            raise ValueError(self.INVALID)

    @classmethod
    def read(cls, payload, explicit=None):
        if explicit is not None:
            if not isinstance(explicit, dict) or explicit.keys() != {item.name for item in fields(cls)}:
                raise ValueError(cls.INVALID)
            return cls(**explicit)
        state = payload.get(cls.status.__name__)
        if state not in tuple(CheckpointStatus):
            raise ValueError(cls.PRIVATE_STATE)
        return cls(state if state in cls.STATES else CheckpointStatus.COMPLETED,
                   state != CheckpointStatus.STARTED or payload.get(cls.container.__name__) is cls.CONTAINER)


@dataclass(frozen=True, slots=True)
class CheckpointEnvelope:
    """Common nested identity and lifecycle; the child owns the opaque payload."""

    KIND_FIELD: ClassVar[str] = "node_type"
    KIND: ClassVar[str] = "engine_record"
    PAYLOAD_FIELD: ClassVar[str] = "payload"
    INVALID_IDENTITY: ClassVar[str] = "Checkpoint envelope requires scope, name and local key"
    INVALID_LIFECYCLE: ClassVar[str] = "Invalid checkpoint envelope lifecycle"
    INVALID_KIND: ClassVar[str] = "Expected a nested Engine checkpoint envelope"
    INVALID_PAYLOAD: ClassVar[str] = "Checkpoint envelope requires an object payload"
    engine_scope: str
    checkpoint_name: str
    local_key: str
    status: str
    container: bool
    requires_retry: bool

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.engine_scope, self.checkpoint_name, self.local_key)):
            raise ValueError(self.INVALID_IDENTITY)
        if (self.status not in CheckpointLifecycle.STATES
                or type(self.container) is not bool or type(self.requires_retry) is not bool
                or self.requires_retry != self.retry_required(self.status, self.container)):
            raise ValueError(self.INVALID_LIFECYCLE)

    @staticmethod
    def retry_required(status, container):
        return status == CheckpointStatus.STARTED and not container

    @classmethod
    def for_lifecycle(cls, *, engine_scope, checkpoint_name, local_key, status, container):
        return cls(engine_scope, checkpoint_name, local_key, status, container, cls.retry_required(status, container))

    def to_dict(self):
        return {self.KIND_FIELD: self.KIND, **asdict(self)}

    @classmethod
    def is_record(cls, record):
        return record.get(cls.KIND_FIELD) == cls.KIND

    @classmethod
    def belongs_to(cls, record, scope, name):
        return record.get(cls.engine_scope.__name__) == scope and record.get(cls.checkpoint_name.__name__) == name

    @classmethod
    def from_dict(cls, record):
        if not cls.is_record(record):
            raise ValueError(cls.INVALID_KIND)
        return cls(**{item.name: record.get(item.name) for item in fields(cls)})

    @classmethod
    def read(cls, record):
        envelope = cls.from_dict(record)
        payload = record.get(cls.PAYLOAD_FIELD)
        if not isinstance(payload, dict):
            raise ValueError(cls.INVALID_PAYLOAD)
        return envelope, payload

    @classmethod
    def attach(cls, envelope, payload):
        cls.from_dict(envelope)
        return {**deepcopy(envelope), cls.PAYLOAD_FIELD: deepcopy(payload)}


@dataclass(frozen=True, slots=True)
class CheckpointConversation(JsonValue):
    """Common conversation references; Engine-specific header fields remain Engine-owned."""

    HEADER_FIELD: ClassVar[str] = "header"
    INVALID: ClassVar[str] = "Invalid checkpoint conversation references"
    message_ids: tuple[str, ...]
    input_message_id: str

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if any(not value for value in (*self.message_ids, self.input_message_id)):
            raise ValueError(self.INVALID)

    @classmethod
    def from_header(cls, header):
        if not isinstance(header, dict):
            raise ValueError(cls.INVALID)
        try:
            return cls.from_dict({item.name: header[item.name] for item in fields(cls)})
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(cls.INVALID) from error

    @classmethod
    def from_checkpoint(cls, checkpoint):
        return cls.from_header(checkpoint[cls.HEADER_FIELD])


class CheckpointErrorCode(StrEnum):
    CONFLICT = "checkpoint_conflict"
    PROTECTED = "checkpoint_protected"
    CORRUPT = "checkpoint_corrupt"
    MISSING = "checkpoint_missing"
    DELETED = "checkpoint_deleted"
    CONFIGURATION = "checkpoint_configuration"


@dataclass(frozen=True, slots=True)
class CheckpointRejectionDetails:
    BOUNDARY: ClassVar[str] = "checkpoint"
    boundary: str
    blockers: list[dict]

    def to_dict(self):
        return asdict(self)

    @classmethod
    def blocker_details(cls, records):
        return {cls.blockers.__name__: records}

    @classmethod
    def observed_blockers(cls, details):
        from .contracts import DiagnosticRecordObservation
        records = details.get(cls.blockers.__name__, ())
        return [DiagnosticRecordObservation(item) for item in records if isinstance(item, dict)] if isinstance(records, (list, tuple)) else []


class CheckpointError(CodedError, ValueError):
    """Structured rejection; retains ValueError catch compatibility, never grants access."""

    MESSAGE = "{code}: {message}"

    def __init__(self, code, message, *, blockers=()):
        self.code, self.blockers = code, tuple(blockers)
        super().__init__(self.MESSAGE.format(code=code, message=message))
        self.diagnostic = Diagnostic(code, str(self), details=CheckpointRejectionDetails(
            CheckpointRejectionDetails.BOUNDARY, [item.to_dict() for item in self.blockers]).to_dict())


class CheckpointSelection:
    IMPLICIT_COUNT = 1
    AMBIGUOUS = "Specify a checkpoint name unless exactly one checkpoint exists"
    INVALID_NAME = "Checkpoint name must be nonempty text"


class CheckpointPlanValidation:
    UNKNOWN_ACTION = "Unknown checkpoint action"
    STALE_REVISION = "review a fresh checkpoint plan"
    BLOCKER_SEPARATOR = "; "


def select_checkpoint(names, name=None):
    """Omission selects the sole stored checkpoint, never an Engine preset."""
    if name is None:
        if len(names) != CheckpointSelection.IMPLICIT_COUNT:
            raise ValueError(CheckpointSelection.AMBIGUOUS)
        name = names[0]
    # Explicit names are resolved by the repository. In particular, a Run that
    # failed before checkpoint creation must still report missing, not corrupt.
    if not isinstance(name, str) or not name:
        raise ValueError(CheckpointSelection.INVALID_NAME)
    return name


def checkpoint_lifecycle(payload, lifecycle=None):
    """Public nested checkpoint lifecycle; private payloads may supply an explicit projection.

    Standard records use started/completed/waiting; responded and not_applied
    denote persisted no-replay observations. Unknown private states require
    lifecycle={status, container} on the event, never a parent-specific adapter.
    """
    value = CheckpointLifecycle.read(payload, lifecycle)
    return value.status, value.container


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

    KEY: ClassVar[str] = "resume_abandonment"
    INVALID: ClassVar[str] = "Invalid resume abandonment record"
    INVALID_TIME: ClassVar[str] = "Abandonment time requires a timezone"
    MISSING_REQUEST: ClassVar[str] = "Conversation abandonment requires request_id"
    UNEXPECTED_REQUEST: ClassVar[str] = "Explicit abandonment has no conversation request"
    abandoned_at: str
    reason: str
    request_id: Optional[str] = None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.abandoned_at or self.reason not in tuple(ResumeAbandonmentReason):
            raise ValueError(self.INVALID)
        if datetime.fromisoformat(self.abandoned_at).utcoffset() is None:
            raise ValueError(self.INVALID_TIME)
        if self.reason == ResumeAbandonmentReason.CONVERSATION_DELETED and not self.request_id:
            raise ValueError(self.MISSING_REQUEST)
        if self.reason == ResumeAbandonmentReason.EXPLICIT and self.request_id is not None:
            raise ValueError(self.UNEXPECTED_REQUEST)

    @classmethod
    def for_request(cls, abandoned_at, request_id):
        reason = ResumeAbandonmentReason.CONVERSATION_DELETED if request_id is not None else ResumeAbandonmentReason.EXPLICIT
        return cls(abandoned_at, reason, request_id)

    @classmethod
    def read(cls, metadata):
        return cls.from_dict(metadata[cls.KEY]) if cls.KEY in metadata else None

    @classmethod
    def require(cls, metadata):
        return cls.from_dict(metadata[cls.KEY])

    def store(self, metadata):
        metadata[self.KEY] = self.to_dict()


def require_checkpoint_plan(plan: CheckpointPlan, expected_revision: str, *, action: str) -> None:
    """계획은 권한이 아니다. 소유자가 잠금 안에서 재계산한 계획에만 적용한다."""
    if action not in tuple(CheckpointAction):
        raise ValueError(CheckpointPlanValidation.UNKNOWN_ACTION)
    if not expected_revision or expected_revision != plan.revision:
        raise CheckpointError(CheckpointErrorCode.CONFLICT, CheckpointPlanValidation.STALE_REVISION)
    blockers = plan.abandon_blockers if action == CheckpointAction.ABANDON else plan.delete_blockers
    if blockers:
        raise CheckpointError(CheckpointErrorCode.PROTECTED, CheckpointPlanValidation.BLOCKER_SEPARATOR.join(item.message for item in blockers), blockers=blockers)
