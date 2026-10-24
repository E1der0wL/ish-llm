"""Current external-operation evidence, manifests and public claim projections."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from llm.core.json_data import JsonObject
from llm.services.infrastructure.storage import revision_token


class OperationIdentity:
    MAXIMUM_KEY_LENGTH = 512
    ENSURE_ASCII = False
    INVALID_KEY = "Operation key must contain 1..512 characters"

    @classmethod
    def token(cls, project_id, session_id, key):
        if not isinstance(key, str) or not key or len(key) > cls.MAXIMUM_KEY_LENGTH:
            raise ValueError(cls.INVALID_KEY)
        return hashlib.sha256(
            json.dumps([project_id, session_id, key], ensure_ascii=cls.ENSURE_ASCII).encode()
        ).hexdigest()


class OperationStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    NOT_APPLIED = "not_applied"


class OperationEvidence:
    RESULT = "result"
    EXECUTION = "execution"
    INVALID_TIME = "Operation timestamp must be text"
    NAIVE_TIME = "Operation timestamp requires a timezone"
    MISSING = "Operation evidence is required"

    @classmethod
    def timestamp(cls, value):
        if not isinstance(value, str):
            raise ValueError(cls.INVALID_TIME)
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is None:
            raise ValueError(cls.NAIVE_TIME)
        return stamp

    @classmethod
    def validate(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(cls.MISSING)

    @classmethod
    def validate_result(cls, value):
        JsonObject.validate({cls.RESULT: value})


class OperationRequest:
    NAME = "name"
    ARGUMENTS = "arguments"
    CONTRACT = "contract"

    @classmethod
    def create(cls, name, arguments, contract):
        return {cls.NAME: name, cls.ARGUMENTS: arguments, cls.CONTRACT: contract}

    @classmethod
    def digest(cls, name, arguments, contract):
        return revision_token(cls.create(name, arguments, contract))


class OperationStorage:
    DIRECTORY = "tool_operations"
    MANIFEST = "manifest.json"
    EXTENSION = ".json"

    @classmethod
    def root(cls, session):
        return session.paths.state / cls.DIRECTORY

    @classmethod
    def record_name(cls, token):
        return token + cls.EXTENSION


class OperationManifest:
    SCHEMA_VERSION = 1
    VERSION = "schema_version"
    PROJECT_ID = "project_id"
    SESSION_ID = "session_id"
    RECORDS = "records"
    FIELDS = frozenset((VERSION, PROJECT_ID, SESSION_ID, RECORDS))
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    INVALID = "Invalid operation manifest"
    INVALID_ENTRY = "Invalid operation manifest entry"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, session, version):
        return cls(
            {
                cls.VERSION: version,
                cls.PROJECT_ID: session.project_id,
                cls.SESSION_ID: session.id,
                cls.RECORDS: {},
            }
        )

    @classmethod
    def read(cls, value, session, version):
        if (
            not isinstance(value, dict)
            or set(value) != cls.FIELDS
            or type(value[cls.VERSION]) is not int
            or value[cls.VERSION] != version
            or value[cls.PROJECT_ID] != session.project_id
            or value[cls.SESSION_ID] != session.id
            or not isinstance(value[cls.RECORDS], dict)
        ):
            raise ValueError(cls.INVALID)
        for token, revision in value[cls.RECORDS].items():
            if (
                not isinstance(token, str)
                or not re.fullmatch(cls.DIGEST_PATTERN, token)
                or not isinstance(revision, str)
                or not re.fullmatch(cls.DIGEST_PATTERN, revision)
            ):
                raise ValueError(cls.INVALID_ENTRY)
        return cls(value)

    @property
    def tokens(self):
        return self._value[self.RECORDS].keys()

    @property
    def has_records(self):
        return bool(self._value[self.RECORDS])

    @property
    def filenames(self):
        return {OperationStorage.MANIFEST, *(OperationStorage.record_name(token) for token in self.tokens)}

    def contains(self, token):
        return token in self._value[self.RECORDS]

    def revision(self, token):
        return self._value[self.RECORDS][token]

    def record_revision(self, token, revision):
        self._value[self.RECORDS][token] = revision

    def to_dict(self):
        return self._value


class OperationAttempt:
    RUN_ID = "run_id"
    STEP_ID = "step_id"
    STATUS = "status"
    EVIDENCE = "evidence"
    CREATED_AT = "created_at"
    ENDED_AT = "ended_at"
    FIELDS = frozenset((RUN_ID, STEP_ID, STATUS, EVIDENCE, CREATED_AT, ENDED_AT))
    OWNERS = (RUN_ID, STEP_ID)
    INVALID_FIELDS = "Invalid operation attempt fields"
    UNPROVEN_REUSE = "Only proven not-applied attempts can precede reuse"
    MISSING_OWNER = "Operation attempt requires its owner"

    def __init__(self, start, end):
        self.start, self.end = start, end

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or set(value) != cls.FIELDS:
            raise ValueError(cls.INVALID_FIELDS)
        if value[cls.STATUS] != OperationStatus.NOT_APPLIED:
            raise ValueError(cls.UNPROVEN_REUSE)
        if any(not isinstance(value[key], str) or not value[key] for key in cls.OWNERS):
            raise ValueError(cls.MISSING_OWNER)
        OperationEvidence.validate(value[cls.EVIDENCE])
        start, end = OperationEvidence.timestamp(value[cls.CREATED_AT]), OperationEvidence.timestamp(
            value[cls.ENDED_AT]
        )
        return cls(start, end)

    @classmethod
    def previous(cls, record):
        return {
            cls.RUN_ID: record.run_id,
            cls.STEP_ID: record.step_id,
            cls.STATUS: OperationStatus.NOT_APPLIED,
            cls.EVIDENCE: record.evidence,
            cls.CREATED_AT: record.created_at,
            cls.ENDED_AT: record.ended_at,
        }


class OperationObservation:
    STATUS = "status"
    EVIDENCE = "evidence"
    AT = "at"
    RESULT = OperationEvidence.RESULT
    UNCERTAIN = "uncertain"
    STATUSES = {OperationStatus.COMPLETED, OperationStatus.NOT_APPLIED, UNCERTAIN}
    EXTERNAL = False
    STORED = True
    INVALID_FIELDS = "Invalid external operation observation fields"
    INVALID_STATUS = "Invalid external operation observation"
    INVALID_RESULT = "Only completed observations require result, including explicit null"

    def __init__(self, value):
        self._value = value

    @classmethod
    def read(cls, value, *, stored=EXTERNAL):
        required = {cls.STATUS, cls.EVIDENCE, *([cls.AT] if stored else [])}
        if (
            not isinstance(value, dict)
            or not required <= value.keys()
            or value.keys() - required - {cls.RESULT}
        ):
            raise ValueError(cls.INVALID_FIELDS)
        JsonObject.validate(value)
        if value[cls.STATUS] not in cls.STATUSES:
            raise ValueError(cls.INVALID_STATUS)
        OperationEvidence.validate(value[cls.EVIDENCE])
        if (cls.RESULT in value) != (value[cls.STATUS] == OperationStatus.COMPLETED):
            raise ValueError(cls.INVALID_RESULT)
        return cls(value)

    @property
    def status(self):
        return self._value[self.STATUS]

    @property
    def evidence(self):
        return self._value[self.EVIDENCE]

    @property
    def result(self):
        return self._value[self.RESULT]

    @property
    def at(self):
        return OperationEvidence.timestamp(self._value[self.AT])

    def stored_at(self, at):
        return {**deepcopy(self._value), self.AT: at}


class OperationClaim:
    REUSED = "reused"
    RESULT = OperationEvidence.RESULT
    FRESH = False
    COMPLETE = True

    def __init__(self, value):
        self._value = value

    @classmethod
    def started(cls):
        return {cls.REUSED: cls.FRESH}

    @classmethod
    def completed(cls, result):
        return {cls.REUSED: cls.COMPLETE, cls.RESULT: deepcopy(result)}

    @property
    def reused(self):
        return self._value[self.REUSED]

    @property
    def result(self):
        return self._value.get(self.RESULT)

    @property
    def required_result(self):
        return self._value[self.RESULT]


@dataclass(frozen=True)
class OperationRecord:
    """Current ledger fields; nullable state is explicit, never supplied by a reader."""

    STATUS_FIELD = "status"
    SCHEMA_VERSION = 1
    REQUIRED_IDENTITIES = ("project_id", "session_id", "run_id", "step_id", "name")
    MISSING_IDENTITY = "Operation requires {name}"
    NO_TERMINAL = None
    NO_PREVIOUS = None
    INVALID_FIELDS = "Operation record requires all current fields"
    INVALID_VERSION = "Invalid operation schema version"
    IDENTITY_MISMATCH = "Operation identity mismatch"
    REQUEST_MISMATCH = "Operation request digest mismatch"
    STARTED_RESULT = "Started operation cannot claim a terminal result"
    INVALID_END = "Operation ended before it started"
    NOT_APPLIED_RESULT = "Not-applied operation cannot claim a result"
    INVALID_HISTORY = "Operation requires attempt and observation lists"
    INVALID_ATTEMPT_ORDER = "Operation attempt chronology mismatch"
    INVALID_OBSERVATION_ORDER = "Operation observations are out of order"

    schema_version: int
    project_id: str
    session_id: str
    key: str
    idempotency_key: str
    request_digest: str
    name: str
    arguments: dict
    contract: dict
    status: str
    run_id: str
    step_id: str
    created_at: str
    ended_at: str | None
    evidence: str | None
    result: Any
    attempts: list
    observations: list

    @classmethod
    def status_from_record(cls, value):
        """Required scalar observation after the selected Repository's current Reader."""
        return value[cls.STATUS_FIELD]

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        JsonObject.validate(value)
        return cls(**deepcopy(value))

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(self.INVALID_VERSION)
        for name in self.REQUIRED_IDENTITIES:
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(self.MISSING_IDENTITY.format(name=name))
        if self.idempotency_key != OperationIdentity.token(self.project_id, self.session_id, self.key):
            raise ValueError(self.IDENTITY_MISMATCH)
        from llm.core.tool_contracts import ToolContract

        ToolContract.from_dict(self.contract)
        if not isinstance(self.arguments, dict) or self.request_digest != OperationRequest.digest(
            self.name, self.arguments, self.contract
        ):
            raise ValueError(self.REQUEST_MISMATCH)
        status = OperationStatus(self.status)
        created = OperationEvidence.timestamp(self.created_at)
        if status == OperationStatus.STARTED:
            if (
                self.ended_at is not self.NO_TERMINAL
                or self.evidence is not self.NO_TERMINAL
                or self.result is not self.NO_TERMINAL
            ):
                raise ValueError(self.STARTED_RESULT)
        else:
            OperationEvidence.validate(self.evidence)
            if OperationEvidence.timestamp(self.ended_at) < created:
                raise ValueError(self.INVALID_END)
            if status == OperationStatus.NOT_APPLIED and self.result is not self.NO_TERMINAL:
                raise ValueError(self.NOT_APPLIED_RESULT)
        if not isinstance(self.attempts, list) or not isinstance(self.observations, list):
            raise ValueError(self.INVALID_HISTORY)
        previous_end = self.NO_PREVIOUS
        for value in self.attempts:
            attempt = OperationAttempt.read(value)
            start, end = attempt.start, attempt.end
            if (
                end < start
                or end > created
                or (previous_end is not self.NO_PREVIOUS and start < previous_end)
            ):
                raise ValueError(self.INVALID_ATTEMPT_ORDER)
            previous_end = end
        previous_at = self.NO_PREVIOUS
        for value in self.observations:
            observation = OperationObservation.read(value, stored=OperationObservation.STORED)
            at = observation.at
            if previous_at is not self.NO_PREVIOUS and at < previous_at:
                raise ValueError(self.INVALID_OBSERVATION_ORDER)
            previous_at = at

    @staticmethod
    def validate_observation(value, *, stored=OperationObservation.EXTERNAL):
        OperationObservation.read(value, stored=stored)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def initial(cls, session, call, digest, at):
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            project_id=session.project_id,
            session_id=session.id,
            key=call.operation_key,
            idempotency_key=call.idempotency_key,
            request_digest=digest,
            name=call.name,
            arguments=deepcopy(call.arguments),
            contract=deepcopy(call.contract),
            status=OperationStatus.STARTED,
            run_id=call.run_id,
            step_id=call.step_id,
            created_at=at,
            ended_at=cls.NO_TERMINAL,
            evidence=cls.NO_TERMINAL,
            result=cls.NO_TERMINAL,
            attempts=[],
            observations=[],
        )

    def restart(self, call, at):
        return replace(
            self,
            attempts=[*self.attempts, OperationAttempt.previous(self)],
            status=OperationStatus.STARTED,
            run_id=call.run_id,
            step_id=call.step_id,
            created_at=at,
            ended_at=self.NO_TERMINAL,
            evidence=self.NO_TERMINAL,
            result=self.NO_TERMINAL,
        )

    def completed(self, result, evidence, at):
        return replace(
            self, status=OperationStatus.COMPLETED, result=deepcopy(result), ended_at=at, evidence=evidence
        )

    def not_applied(self, evidence, at):
        return replace(self, status=OperationStatus.NOT_APPLIED, ended_at=at, evidence=evidence)

    def observed(self, observation, at):
        return replace(self, observations=[*self.observations, observation.stored_at(at)])


@dataclass(frozen=True, slots=True)
class OperationVerificationPreview:
    """The original uncommitted probe result; only applying invokes ledger validation."""

    operation: dict
    observation: Any
    version: str

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}
