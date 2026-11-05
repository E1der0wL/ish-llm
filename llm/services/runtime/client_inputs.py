"""Caller-key receipts on the existing conversation, not a second execution ledger.

Owners persist this metadata in the same record transaction as admission/response.
It proves acceptance only: it cannot grant approval, replay an effect or resume a Run.
File/memory durability is exactly that of the selected conversation store.
"""

from dataclasses import asdict, dataclass, fields
from copy import deepcopy
from enum import StrEnum
from typing import ClassVar

from llm.core.contracts import JsonValue
from llm.core.json_data import JsonObject
from llm.core.configuration import UNSET
from llm.core.interactions import InteractionRequest
from llm.services.infrastructure.storage import revision_token


class InputKind(StrEnum):
    REQUEST = 'request'
    INSTRUCTION = 'instruction'
    RESERVATION = 'reservation'
    INTERACTION = 'interaction'
    REDIRECT = 'redirect'
    REPLACEMENT = 'replacement'


@dataclass(frozen=True, slots=True)
class InputIdentity(JsonValue):
    key: str
    project_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class InputFingerprint:
    kind: str
    payload: dict


@dataclass(frozen=True, slots=True)
class RequestPayload:
    content: str
    recipients: object
    request_context: object = UNSET

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)
                if getattr(self, item.name) is not UNSET}


@dataclass(frozen=True, slots=True)
class InstructionPayload:
    UNSPECIFIED_TARGETS: ClassVar[None] = None
    run_id: str
    content: str
    targets: object


@dataclass(frozen=True, slots=True)
class ReplacementPayload:
    run_id: str
    instruction_id: str
    content: str | None


@dataclass(frozen=True, slots=True)
class ResponsePayload:
    run_id: str
    response: dict
    remember: object = UNSET


@dataclass(frozen=True, slots=True)
class InputReceipt(InputIdentity):
    UNKNOWN_KIND = 'Unknown client input receipt kind'
    MISSING_IDENTITY = 'Client input receipt requires its persisted identity'
    PREMATURE_RUN = 'Request acceptance does not select a Run'
    MISSING_RUN = 'Client input receipt requires its Run'
    MISSING_REVISION = 'Interaction acceptance requires the exact request revision'
    UNEXPECTED_DECISION = 'Only an interaction receipt may bind a decision'
    INVALID_KEY = 'Client request key must be a nonempty string'
    KEY_CONFLICT = 'Client request key was already accepted with different content or targets'
    INVALID_METADATA = 'Invalid client input receipt metadata'
    INVALID_OWNER = 'Client input receipt ownership mismatch'
    DUPLICATE = 'Duplicate client input receipt'
    ALREADY_ACCEPTED = 'Client input receipt already exists'

    METADATA: ClassVar[str] = 'client_input_receipts'
    MAXIMUM_MATCHES: ClassVar[int] = 1  # One accepted receipt per caller key, never a user limit.
    kind: str
    fingerprint: str
    message_id: str
    run_id: str | None
    interaction_id: str | None
    interaction_revision: int | None

    def __post_init__(self):
        super(InputReceipt, self).__post_init__()
        self.validate_key(self.key)
        if self.kind not in InputKind:
            raise ValueError(InputReceipt.UNKNOWN_KIND)
        if any(not isinstance(value, str) or not value for value in (
            self.project_id, self.session_id, self.message_id, self.fingerprint
        )):
            raise ValueError(InputReceipt.MISSING_IDENTITY)
        if self.kind == InputKind.REQUEST:
            if self.run_id is not None:
                raise ValueError(InputReceipt.PREMATURE_RUN)
        elif not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError(InputReceipt.MISSING_RUN)
        if self.kind == InputKind.INTERACTION:
            if not self.interaction_id or type(self.interaction_revision) is not int or self.interaction_revision < InteractionRequest.INITIAL_REVISION:
                raise ValueError(InputReceipt.MISSING_REVISION)
        elif self.interaction_id is not None or self.interaction_revision is not None:
            raise ValueError(InputReceipt.UNEXPECTED_DECISION)

    @staticmethod
    def validate_key(key):
        if not isinstance(key, str) or not key.strip():
            raise ValueError(InputReceipt.INVALID_KEY)

    @classmethod
    def token(cls, session, key):
        cls.validate_key(key)
        return revision_token(InputIdentity(key, session.project_id, session.id).to_dict())

    @staticmethod
    def bind(kind, payload):
        JsonObject.validate(payload)
        return revision_token(asdict(InputFingerprint(kind, payload)))

    @staticmethod
    def request_payload(content, recipients, request_context=UNSET):
        return RequestPayload(content, list(recipients) if isinstance(recipients, tuple) else recipients,
                              request_context).to_dict()

    @staticmethod
    def instruction_payload(run_id, content, targets, *, reserved):
        selected = ([value.to_dict() for value in targets] if reserved else
                    list(targets) if targets is not InstructionPayload.UNSPECIFIED_TARGETS else InstructionPayload.UNSPECIFIED_TARGETS)
        return asdict(InstructionPayload(run_id, content, selected))

    @staticmethod
    def replacement_payload(run_id, identifier, content):
        return asdict(ReplacementPayload(run_id, identifier, content))

    def require(self, kind, payload):
        if self.kind != kind or self.fingerprint != self.bind(kind, payload):
            raise ValueError(InputReceipt.KEY_CONFLICT)

    @classmethod
    def find(cls, store, session, key):
        token = cls.token(session, key)
        matches = []
        # This opt-in lookup uses the store's projection (including deleted turns).
        # No key/TTL is invented for ordinary submissions, and clones cannot reuse
        # another Session's receipts. There is no separate durable index authority.
        for message in store.list():
            values = message.metadata.get(cls.METADATA, {})
            if not isinstance(values, dict):
                raise ValueError(InputReceipt.INVALID_METADATA)
            if token in values:
                item = cls.from_dict(values[token])
                if (item.project_id, item.session_id, item.key, item.message_id) != (
                    session.project_id, session.id, key, message.id
                ):
                    raise ValueError(InputReceipt.INVALID_OWNER)
                matches.append(item)
        if len(matches) > cls.MAXIMUM_MATCHES:
            raise ValueError(InputReceipt.DUPLICATE)
        return matches[0] if matches else None

    @classmethod
    def record(cls, store, session, message_id, key, kind, payload, *, run_id=None,
               interaction_id=None, interaction_revision=None):
        token = cls.token(session, key)
        item = cls(key, session.project_id, session.id, kind, cls.bind(kind, payload),
                   message_id, run_id, interaction_id, interaction_revision)
        message = store.get(message_id)
        values = dict(message.metadata.get(cls.METADATA, {}))
        if token in values:
            raise ValueError(InputReceipt.ALREADY_ACCEPTED)
        values[token] = item.to_dict()
        store.update_metadata(message_id, {cls.METADATA: values})
        return item


def response_payload(run_id, response, *, remember=UNSET):
    # Receipt creation time is not the user's decision. Repeated equivalent
    # responses still bind the exact request revision/fingerprint and choice.
    value = ResponsePayload(run_id, response.comparable(), remember)
    return {item.name: deepcopy(getattr(value, item.name)) for item in fields(value)
            if getattr(value, item.name) is not UNSET}
