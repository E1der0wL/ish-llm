"""Owned transfer identities, immutable receipts and observation projections."""

from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
from pathlib import Path

from llm.core.contracts import JsonValue
from llm.core.models import ProjectConfig
from llm.errors import CodedError
from llm.services.infrastructure.storage import child, revision_token
from llm.services.runtime.requests import RequestAdmission


class TransferExpired(CodedError, ValueError):
    code = "transfer_expired"


class TransferDenied(CodedError, PermissionError):
    code = "transfer_denied"


class TransferAction(StrEnum):
    SEND = "send"
    INSPECT = "inspect"
    QUERY = "query"
    CANCEL = "cancel"
    RESUME = "resume"
    EXECUTE = "execute"


class TransferDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"


class TransferDeadline:
    ABSENT = None
    EXPIRED_SECONDS = 0
    INVALID_TYPE = "Transfer deadline must be timezone-aware ISO text"
    MISSING_TIMEZONE = "Transfer deadline requires a timezone"
    EXPIRED = "Session transfer deadline expired"
    NONCANONICAL = "Transfer timestamps must be canonical UTC"

    @classmethod
    def read(cls, value):
        if value is cls.ABSENT:
            return cls.ABSENT
        if not isinstance(value, str):
            raise TypeError(cls.INVALID_TYPE)
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is None:
            raise ValueError(cls.MISSING_TIMEZONE)
        return stamp.astimezone(timezone.utc).isoformat()

    @classmethod
    def canonical(cls, value):
        if value != cls.read(value):
            raise ValueError(cls.NONCANONICAL)

    @classmethod
    def remaining(cls, value):
        if value is cls.ABSENT:
            return cls.ABSENT
        seconds = (datetime.fromisoformat(value) - datetime.now(timezone.utc)).total_seconds()
        if seconds <= cls.EXPIRED_SECONDS:
            raise TransferExpired(cls.EXPIRED)
        return seconds


class TransferIdentity:
    INVALID_KEY = "Transfer idempotency key must be nonempty text"

    @classmethod
    def create(cls, project_id, session_id, key):
        if not isinstance(key, str) or not key.strip():
            raise ValueError(cls.INVALID_KEY)
        return revision_token([project_id, session_id, key])


class TransferContent:
    INVALID_TEXT = "Transfer content must be text"
    INVALID_DIGEST = "Invalid transfer content digest"
    DIGEST_LENGTH = 64
    HEX_DIGITS = frozenset("0123456789abcdef")
    ENCODING = "utf-8"

    @classmethod
    def digest(cls, content):
        if not isinstance(content, str):
            raise TypeError(cls.INVALID_TEXT)
        return hashlib.sha256(content.encode(cls.ENCODING)).hexdigest()

    @classmethod
    def validate(cls, digest):
        if not isinstance(digest, str) or len(digest) != cls.DIGEST_LENGTH or set(digest) - cls.HEX_DIGITS:
            raise ValueError(cls.INVALID_DIGEST)


@dataclass(frozen=True, slots=True)
class TransferSource(JsonValue):
    HOST_REFERENCE = None
    INVALID_HOST = "Host source cannot claim a Run identity"

    project_id: str
    session_id: str
    run_id: str | None
    agent_id: str | None
    message_id: str | None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        child(Path(), self.project_id)
        child(Path(), self.session_id)
        if self.run_id is self.HOST_REFERENCE:
            if self.agent_id is not self.HOST_REFERENCE or self.message_id is not self.HOST_REFERENCE:
                raise ValueError(self.INVALID_HOST)
        else:
            child(Path(), self.run_id)
            child(Path(), self.message_id)
            RequestAdmission.identifiers([self.agent_id])

    @classmethod
    def host(cls, project_id, session_id):
        return cls(project_id, session_id, cls.HOST_REFERENCE, cls.HOST_REFERENCE, cls.HOST_REFERENCE)

    @classmethod
    def from_run(cls, project_id, session_id, run):
        return cls(project_id, session_id, run.id, run.agent_id, run.input_message_id)


@dataclass(frozen=True, slots=True)
class TransferIntent(JsonValue):
    """Pre-admission payload; it carries no accepted Message or receipt authority."""

    SCHEMA_VERSION = 1
    MINIMUM_CHAIN = 2
    INVALID_FIELDS = "Transfer requires every current receipt field"
    INVALID_RECIPIENTS = "Transfer recipients must be canonical"
    INVALID_CHAIN = "Transfer cycle or inconsistent lineage"
    INVALID_IDENTITY = "Transfer identity mismatch"
    IDEMPOTENCY_CONFLICT = "Transfer idempotency key conflicts with its original payload"

    schema_version: int
    id: str
    source: TransferSource
    target_session_id: str
    recipients: tuple[str, ...]
    key: str
    content_digest: str
    deadline: str | None
    chain: tuple[str, ...]

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(self.INVALID_FIELDS)
        child(Path(), self.target_session_id)
        if self.recipients != tuple(RequestAdmission.identifiers(list(self.recipients))):
            raise ValueError(self.INVALID_RECIPIENTS)
        if (len(self.chain) < self.MINIMUM_CHAIN or len(set(self.chain)) != len(self.chain)
                or self.chain[-self.MINIMUM_CHAIN:] != (self.source.session_id, self.target_session_id)):
            raise ValueError(self.INVALID_CHAIN)
        for identifier in self.chain:
            child(Path(), identifier)
        TransferDeadline.canonical(self.deadline)
        if self.id != TransferIdentity.create(self.source.project_id, self.source.session_id, self.key):
            raise ValueError(self.INVALID_IDENTITY)
        TransferContent.validate(self.content_digest)

    def require_same_payload(self, receipt):
        if any(getattr(receipt, item.name) != getattr(self, item.name) for item in fields(TransferIntent)):
            raise ValueError(self.IDEMPOTENCY_CONFLICT)


@dataclass(frozen=True, slots=True)
class TransferReceipt(TransferIntent):
    KEY = "transfer"
    CLONE_KEY = "cloned_transfer"
    ABSENT = None
    INVALID_REVISION = "Transfer receipt revision mismatch"

    target_message_id: str
    accepted_at: str
    revision: str

    def __post_init__(self):
        TransferIntent.__post_init__(self)
        child(Path(), self.target_message_id)
        TransferDeadline.canonical(self.accepted_at)
        self.require_revision(self.to_dict())

    @classmethod
    def require_revision(cls, value):
        if value[cls.revision.__name__] != revision_token({
                key: item for key, item in value.items() if key != cls.revision.__name__}):
            raise ValueError(cls.INVALID_REVISION)

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        ProjectConfig.validate_json(value)
        # Validate the supplied raw bytes' JSON meaning before decoding a projection.
        cls.require_revision(value)
        try:
            return cls.from_dict(value)
        except TypeError as error:
            raise ValueError(cls.INVALID_FIELDS) from error

    @classmethod
    def accept(cls, intent, message_id, accepted_at):
        value = {**intent.to_dict(), cls.target_message_id.__name__: message_id,
                 cls.accepted_at.__name__: accepted_at}
        value[cls.revision.__name__] = revision_token(value)
        return cls.read(value)

    @classmethod
    def present(cls, metadata):
        return cls.KEY in metadata

    @classmethod
    def from_metadata(cls, metadata):
        value = metadata.get(cls.KEY)
        return cls.ABSENT if value is cls.ABSENT else cls.read(value)

    @classmethod
    def require(cls, metadata):
        return cls.read(metadata[cls.KEY])

    def metadata(self):
        return {self.KEY: self.to_dict()}

    @classmethod
    def clone_metadata(cls, metadata):
        receipt = cls.require(metadata)
        result = deepcopy(metadata)
        result.pop(cls.KEY)
        result[cls.CLONE_KEY] = TransferObservation(receipt.id, receipt.revision).to_dict()
        return result


@dataclass(frozen=True, slots=True)
class TransferObservation(JsonValue):
    id: str
    revision: str


@dataclass(frozen=True, slots=True)
class TransferAuthorization(JsonValue):
    ABSENT_RECEIPT = None

    action: TransferAction
    project_id: str
    source_session_id: str
    target_session_id: str
    receipt: dict | None


@dataclass(frozen=True, slots=True)
class TransferSessionView(JsonValue):
    session_id: str
    title: str
    agent_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransferResultView(JsonValue):
    message_id: str
    text: str
    status: str


@dataclass(frozen=True, slots=True)
class TransferRunView(JsonValue):
    run_id: str
    agent_id: str
    status: str
    input_message_id: str
    error_code: str | None
    result: TransferResultView


@dataclass(frozen=True, slots=True)
class TransferRequestView(JsonValue):
    message_id: str
    status: str
    run_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransferSnapshot(JsonValue):
    receipt: TransferReceipt
    requests: tuple[TransferRequestView, ...]
    runs: tuple[TransferRunView, ...]


@dataclass(frozen=True, init=False, slots=True)
class TransferInventory:
    """A validated immutable ledger; updates serialize the original CAS revision."""

    SCHEMA_VERSION = 1
    INVALID_RECORD = "Transfer ledger ownership, fields or revision mismatch"
    INVALID_INVENTORY = "Transfer inventory must be an object"
    INVALID_ENTRY = "Transfer inventory entry belongs to another owner"
    CHANGED = "Transfer ledger changed; reload its original revision"

    class Field(StrEnum):
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        SESSION_ID = "session_id"
        REVISION = "revision"

    FIELDS = frozenset((*Field, *TransferDirection))
    _value: dict

    @classmethod
    def read(cls, value, *, project_id, session_id):
        if (not isinstance(value, dict) or value.keys() != cls.FIELDS
                or type(value[cls.Field.SCHEMA_VERSION]) is not int
                or value[cls.Field.SCHEMA_VERSION] != cls.SCHEMA_VERSION
                or (value[cls.Field.PROJECT_ID], value[cls.Field.SESSION_ID]) != (project_id, session_id)
                or value[cls.Field.REVISION] != revision_token({
                    key: item for key, item in value.items() if key != cls.Field.REVISION})):
            raise ValueError(cls.INVALID_RECORD)
        for direction in TransferDirection:
            if not isinstance(value[direction], dict):
                raise ValueError(cls.INVALID_INVENTORY)
            for identifier, raw in value[direction].items():
                receipt = TransferReceipt.read(raw)
                owner = receipt.source.session_id if direction == TransferDirection.OUTGOING else receipt.target_session_id
                if identifier != receipt.id or owner != session_id or receipt.source.project_id != project_id:
                    raise ValueError(cls.INVALID_ENTRY)
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        return result

    @classmethod
    def empty(cls, *, project_id, session_id):
        value = {cls.Field.SCHEMA_VERSION: cls.SCHEMA_VERSION,
                 cls.Field.PROJECT_ID: project_id, cls.Field.SESSION_ID: session_id,
                 TransferDirection.OUTGOING: {}, TransferDirection.INCOMING: {}}
        return cls.committed(value, project_id=project_id, session_id=session_id)

    @classmethod
    def committed(cls, value, *, project_id, session_id):
        """Writer-only revision assignment; readers always require the saved revision."""
        value = deepcopy(value)
        value.pop(cls.Field.REVISION, None)
        value[cls.Field.REVISION] = revision_token(value)
        return cls.read(value, project_id=project_id, session_id=session_id)

    @property
    def project_id(self):
        return self._value[self.Field.PROJECT_ID]

    @property
    def session_id(self):
        return self._value[self.Field.SESSION_ID]

    @property
    def revision(self):
        return self._value[self.Field.REVISION]

    @property
    def populated(self):
        return any(self._value[direction] for direction in TransferDirection)

    def entries(self, direction):
        return {identifier: TransferReceipt.read(raw)
                for identifier, raw in self._value[TransferDirection(direction)].items()}

    def require_update(self, value):
        if value.get(self.Field.REVISION) != self.revision:
            raise ValueError(self.CHANGED)

    def with_receipt(self, direction, receipt):
        """Serialize a Repository update, carrying the unchanged original CAS revision."""
        value = self.to_dict()
        value[TransferDirection(direction)][receipt.id] = receipt.to_dict()
        committed = self.committed(value, project_id=self.project_id, session_id=self.session_id).to_dict()
        committed[self.Field.REVISION] = self.revision
        return committed

    def to_dict(self):
        return deepcopy(self._value)
