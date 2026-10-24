"""Current interaction control evidence and deterministic retry-request publication."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
import hashlib
import json

from llm.core.interactions import (
    InteractionRequest,
    InteractionResponse,
    InteractionStatus,
    InteractionBinding,
    approval_request,
    same_interaction_value,
)
from llm.core.json_data import JsonObject


class InteractionStorage:
    DIRECTORY = "interactions"
    EXTENSION = ".json"
    RECORDS = "*.json"

    @classmethod
    def root(cls, run):
        return run.paths.state / cls.DIRECTORY


class RetryInteraction:
    CATEGORY = "execution.retry_uncertain"
    TARGET = "retry_nodes"
    TITLE = "결과가 불확실한 작업 재실행"
    RUN_ID = "run_id"
    SESSION_ID = "session_id"
    CHECKPOINT_RECORD = "checkpoint_record"
    SORT_KEYS = True
    ALLOW_NAN = False

    @classmethod
    def request(cls, checkpoint, run, key, record):
        request = approval_request(
            cls.TITLE,
            category=cls.CATEGORY,
            source={cls.RUN_ID: run.id, cls.SESSION_ID: run.session_id},
            action={cls.CHECKPOINT_RECORD: deepcopy(record)},
        )
        identity = hashlib.sha256(
            json.dumps(
                [run.id, checkpoint.name, key, record], sort_keys=cls.SORT_KEYS, allow_nan=cls.ALLOW_NAN
            ).encode()
        ).hexdigest()[: InteractionRequest.ID_LENGTH]
        return replace(
            request,
            id=identity,
            created_at=run.created_at,
            binding=InteractionBinding.for_retry(checkpoint.name, key, cls.TARGET).to_dict(),
        )


class InteractionControlValue:
    """Shared projections and Writers of the canonical InteractionRecord fields."""

    __slots__ = ()

    @property
    def has_response(self):
        return self.response is not InteractionRecord.NO_RESPONSE

    @property
    def has_replacement(self):
        return self.replacement is not InteractionRecord.NO_REPLACEMENT

    @property
    def parsed_response(self):
        return InteractionResponse.from_dict(self.response)

    @property
    def parsed_replacement(self):
        return InteractionRequest.from_dict(self.replacement)

    def for_run(self, identifier):
        return {**self.to_dict(), InteractionRecord.run_id.__name__: identifier}

    def cancelled_record(self):
        value = self.to_dict()
        value[InteractionRecord.cancelled.__name__] = InteractionRecord.CANCELLED
        return value

    def renewed_record(self, replacement):
        value = self.to_dict()
        value[InteractionRecord.replacement.__name__] = replacement.to_dict()
        return value

    def responded_record(self, response):
        return {**self.to_dict(), InteractionRecord.response.__name__: response.to_dict()}

    def response_for(self, request):
        response = self.parsed_response
        if (
            response.request_fingerprint != request.fingerprint
            or response.request_id != request.id
            or response.request_revision != request.revision
            or response.option_id not in {option.id for option in request.options}
        ):
            raise ValueError(InteractionRecord.CHANGED_RESPONSE_SELECTION)
        return response


@dataclass(frozen=True, slots=True)
class InteractionRecord(InteractionControlValue):
    """The complete persisted control state, including an unanswered request."""

    STORAGE_VERSION = 1
    CANCELLED = True
    ACTIVE = False
    NO_RESPONSE = None
    NO_REPLACEMENT = None
    UNSUPPORTED_VERSION = "Unsupported interaction storage_version"
    MISSING_RUN = "Interaction record requires its Run"
    INVALID_REVISION = "Invalid interaction record revision"
    INVALID_CANCELLATION = "Interaction cancellation must be a boolean"
    CHANGED_RESPONSE = "Interaction response identity changed"
    INVALID_RENEWAL = "Invalid interaction renewal chain"
    INVALID_FIELDS = "Interaction record fields changed or are incomplete"
    CHANGED_REQUEST = "Interaction request changed after publication"
    CHANGED_RENEWAL = "Renewal cannot change the execution target or choices"
    MISSING_RENEWAL_EVIDENCE = "Renewal requires cancellation or expiry evidence"
    CHANGED_RESPONSE_SELECTION = "Interaction changed after responding"

    storage_version: int
    run_id: str
    request_id: str
    request_revision: int
    request_fingerprint: str
    cancelled: bool
    response: dict | None
    replacement: dict | None

    def __post_init__(self):
        if type(self.storage_version) is not int or self.storage_version != self.STORAGE_VERSION:
            raise ValueError(self.UNSUPPORTED_VERSION)
        InteractionRequest.validate_identity(self.request_id)
        InteractionRequest.validate_fingerprint(self.request_fingerprint)
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError(self.MISSING_RUN)
        if (
            type(self.request_revision) is not int
            or self.request_revision < InteractionRequest.INITIAL_REVISION
        ):
            raise ValueError(self.INVALID_REVISION)
        if type(self.cancelled) is not bool:
            raise ValueError(self.INVALID_CANCELLATION)
        if self.has_response:
            response = self.parsed_response
            if (
                response.request_id != self.request_id
                or response.request_revision != self.request_revision
                or response.request_fingerprint != self.request_fingerprint
            ):
                raise ValueError(self.CHANGED_RESPONSE)
        if self.has_replacement:
            replacement = self.parsed_replacement
            if (
                replacement.id == self.request_id
                or replacement.revision != InteractionRequest.renewal_revision(self.request_revision)
                or replacement.status != InteractionStatus.PENDING
            ):
                raise ValueError(self.INVALID_RENEWAL)

    @classmethod
    def initial(cls, run, request):
        return cls(
            cls.STORAGE_VERSION,
            run.id,
            request.id,
            request.revision,
            request.fingerprint,
            cls.ACTIVE,
            cls.NO_RESPONSE,
            cls.NO_REPLACEMENT,
        )

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        JsonObject.validate(value)
        return cls(**deepcopy(value))

    def to_dict(self):
        return asdict(self)

    def validate_request(self, request):
        if (
            request.id != self.request_id
            or request.revision != self.request_revision
            or request.fingerprint != self.request_fingerprint
        ):
            raise ValueError(self.CHANGED_REQUEST)
        if self.has_response:
            response = self.parsed_response
            request.decision_for(response.option_id, value=response.value)
        if self.has_replacement:
            replacement = self.parsed_replacement
            expected = request.renewal(
                replacement.id, created_at=replacement.created_at, expires_at=replacement.expires_at
            )
            if not same_interaction_value(replacement.to_dict(), expected.to_dict()):
                raise ValueError(self.CHANGED_RENEWAL)
            if not self.cancelled and (
                request.expires_at is InteractionRequest.NO_EXPIRY
                or datetime.fromisoformat(request.expires_at) > datetime.fromisoformat(replacement.created_at)
            ):
                raise ValueError(self.MISSING_RENEWAL_EVIDENCE)


class InteractionControl(InteractionControlValue):
    """Projection of the existing envelope boundary; not a second storage Reader.

    The Repository envelope validates complete persisted records before returning
    its detached dictionary. This projection adds no fallback or schema repair.
    """

    def __init__(self, envelope):
        self._value = envelope

    @property
    def response(self):
        return self._value[InteractionRecord.response.__name__]

    @property
    def replacement(self):
        return self._value[InteractionRecord.replacement.__name__]

    @property
    def cancelled(self):
        return self._value[InteractionRecord.cancelled.__name__]

    def to_dict(self):
        return deepcopy(self._value)
