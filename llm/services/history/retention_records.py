"""Typed History retention projections and the existing public record Writers."""

import json

from llm.core.contracts import ResourceRef
from llm.core.model_protocol import ModelMessage, ModelRequest
from llm.core.models import Run
from llm.core.paths import SessionPaths
from llm.core.plans import RetentionPlan, RetentionUnit
from llm.services.infrastructure.storage import record, tree_snapshot
from .maintenance_records import HistoryEvidence, HistoryIssueCode


class RetentionProtection:
    RUNTIME_ATTACHED = HistoryIssueCode.RUNTIME_ATTACHED
    NON_FILE_CONVERSATION = "non_file_conversation"
    PENDING_MESSAGE = "pending_message"
    PINNED_OR_COMPONENT_REFERENCE = "pinned_or_component_reference"
    PENDING_MAINTENANCE = "pending_maintenance"
    EXTERNAL_OPERATION_HISTORY = "external_operation_history"
    UNFINISHED_OR_RESUMABLE_RUN = "unfinished_or_resumable_run"
    RECENT_OR_REFERENCED = "recent_or_referenced"
    ACTIVE_USAGE_ACCOUNTING = "active_usage_accounting"
    PROTECTED_REQUEST_GROUP = "protected_request_group"
    COMPONENT_REFERENCE = "component_reference"
    PINNED = "pinned"


class HistoryMeasurement:
    ALL_FILES = "*"
    LOG_DIRECTORY = SessionPaths.LOGS
    ENCODING = "utf-8"
    ENSURE_ASCII = False

    @classmethod
    def stamps(cls, root):
        excluded = {
            str(path.relative_to(root))
            for path in root.rglob(cls.ALL_FILES)
            if cls.LOG_DIRECTORY in path.relative_to(root).parts
        }
        return tree_snapshot(root, exclude=excluded)

    @classmethod
    def message_bytes(cls, messages):
        return sum(
            len(json.dumps(record(message), ensure_ascii=cls.ENSURE_ASCII).encode(cls.ENCODING))
            for message in messages
        )


class RetentionCounter:
    MESSAGES = ModelRequest.messages.__name__
    ROLE = ModelMessage.role.__name__
    CONTENT = ModelMessage.content.__name__
    ABSENT = None
    MINIMUM_COUNT = 0
    INVALID_PARAMETERS = "Retention counter parameters cannot override messages"
    INVALID_COUNTER = "Retention requires an explicitly selected callable counter"
    MISSING_COUNTER = "Retention counter parameters require a selected counter"
    INVALID_COUNT = "Retention counter requires nonnegative integer tokens"

    @classmethod
    def validate_parameters(cls, parameters):
        if not isinstance(parameters, dict) or cls.MESSAGES in parameters:
            raise ValueError(cls.INVALID_PARAMETERS)

    @classmethod
    def validate_selection(cls, counter, parameters):
        if counter is not cls.ABSENT and not callable(counter):
            raise ValueError(cls.INVALID_COUNTER)
        if parameters and counter is cls.ABSENT:
            raise ValueError(cls.MISSING_COUNTER)

    @classmethod
    def estimate(cls, counter, parameters, messages):
        # This is the explicit estimation callable's existing minimal projection.
        # ModelMessage.to_dict() is a different, complete versioned record.
        tokens = counter(
            {
                **parameters,
                cls.MESSAGES: [
                    {cls.ROLE: message.role.value, cls.CONTENT: message.content} for message in messages
                ],
            }
        )
        if type(tokens) is not int or tokens < cls.MINIMUM_COUNT:
            raise ValueError(cls.INVALID_COUNT)
        return tokens


class RetentionRow:
    SESSION_ID = ResourceRef.session_id.__name__
    RUN_ID = ResourceRef.run_id.__name__
    STATUS = Run.status.__name__
    GROUP_RUN_IDS = "group_run_ids"
    MESSAGE_IDS = "message_ids"
    MESSAGE_REVISIONS = "message_revisions"
    BYTES = RetentionPlan.bytes.__name__
    TOKENS = RetentionPlan.tokens.__name__
    LAST_ACTIVITY = "last_activity"
    REVISION = "revision"
    RECORD_REVISION = "record_revision"
    REASONS = "reasons"

    def __init__(self, value):
        self._value = value

    @classmethod
    def read_many(cls, rows):
        return (cls(row) for row in rows)

    @classmethod
    def run(
        cls,
        *,
        session_id,
        run_id,
        status,
        group_run_ids,
        message_ids,
        message_revisions,
        byte_count,
        tokens,
        last_activity,
        revision,
        record_revision,
    ):
        return cls(
            {
                cls.SESSION_ID: session_id,
                cls.RUN_ID: run_id,
                cls.STATUS: status,
                cls.GROUP_RUN_IDS: group_run_ids,
                cls.MESSAGE_IDS: message_ids,
                cls.MESSAGE_REVISIONS: message_revisions,
                cls.BYTES: byte_count,
                cls.TOKENS: tokens,
                cls.LAST_ACTIVITY: last_activity,
                cls.REVISION: revision,
                cls.RECORD_REVISION: record_revision,
            }
        )

    @classmethod
    def session(cls, *, session_id, byte_count, tokens, last_activity, revision):
        return cls(
            {
                cls.SESSION_ID: session_id,
                cls.BYTES: byte_count,
                cls.TOKENS: tokens,
                cls.LAST_ACTIVITY: last_activity,
                cls.REVISION: revision,
            }
        )

    @property
    def record(self):
        return self._value

    @property
    def session_id(self):
        return self._value[self.SESSION_ID]

    @property
    def run_id(self):
        return self._value[self.RUN_ID]

    @property
    def group_run_ids(self):
        return self._value[self.GROUP_RUN_IDS]

    @property
    def message_revisions(self):
        return self._value[self.MESSAGE_REVISIONS]

    @property
    def record_revision(self):
        return self._value[self.RECORD_REVISION]

    @property
    def reasons(self):
        return self._value.get(self.REASONS)

    def with_reasons(self, reasons):
        return type(self)({**self._value, **({self.REASONS: reasons} if reasons else {})})

    def protect(self, reasons):
        self._value[self.REASONS] = reasons

    def evidence(self):
        value = dict(self._value)
        value.pop(self.TOKENS)
        return value

    def target_key(self, fields):
        return RetentionTarget.identity(self._value, fields)


class RetentionEvidence:
    UNIT = RetentionPlan.unit.__name__
    SOURCES = HistoryEvidence.SOURCES
    CANDIDATES = RetentionPlan.candidates.__name__
    PROTECTED = RetentionPlan.protected.__name__

    @classmethod
    def create(cls, unit, candidates, protected, sources):
        proof = {cls.UNIT: unit, cls.SOURCES: sources, cls.CANDIDATES: [], cls.PROTECTED: []}
        for destination, rows in ((proof[cls.CANDIDATES], candidates), (proof[cls.PROTECTED], protected)):
            for row in rows:
                destination.append(row.evidence())
        return proof


class RetentionTarget:
    SESSION_ID = RetentionRow.SESSION_ID
    RUN_ID = RetentionRow.RUN_ID
    INVALID_COLLECTION = "History pruning requires explicit targets"
    INVALID_FIELDS = "History target fields do not match the explicit unit"
    INVALID_IDENTIFIERS = "History targets require owned identifiers"

    def __init__(self, value):
        self._value = value

    @classmethod
    def validate_collection(cls, targets):
        if not isinstance(targets, (list, tuple)):
            raise ValueError(cls.INVALID_COLLECTION)

    @classmethod
    def fields(cls, unit):
        return {cls.SESSION_ID, cls.RUN_ID} if unit == RetentionUnit.RUN else {cls.SESSION_ID}

    @classmethod
    def read(cls, value, fields):
        if not isinstance(value, dict) or value.keys() != fields:
            raise ValueError(cls.INVALID_FIELDS)
        if any(not isinstance(value[name], str) for name in fields):
            raise ValueError(cls.INVALID_IDENTIFIERS)
        return cls(value)

    @property
    def session_id(self):
        return self._value[self.SESSION_ID]

    @property
    def run_id(self):
        return self._value[self.RUN_ID]

    def key(self, fields):
        return self.identity(self._value, fields)

    @staticmethod
    def identity(value, fields):
        return tuple(value[name] for name in sorted(fields))


class RetentionIntent:
    ID = ResourceRef.id.__name__
    KIND = ResourceRef.kind.__name__
    SESSION_ID = RetentionRow.SESSION_ID
    CREATED_AT = Run.created_at.__name__
    COMPLETED_AT = "completed_at"
    RUNS = "runs"
    RUN_RETENTION = "run_retention"
    INVALID = "Invalid retention journal"

    def __init__(self, value):
        self._value = value

    @classmethod
    def start(cls, identifier, session_id, created_at, rows):
        return cls(
            {
                cls.ID: identifier,
                cls.KIND: cls.RUN_RETENTION,
                cls.SESSION_ID: session_id,
                cls.CREATED_AT: created_at,
                cls.RUNS: [row.record for row in rows],
            }
        )

    def validate(self, session):
        if self._value.get(self.SESSION_ID) != session.id or self._value.get(self.KIND) != self.RUN_RETENTION:
            raise ValueError(self.INVALID)

    @property
    def record(self):
        return self._value

    @property
    def id(self):
        return self._value[self.ID]

    @property
    def runs(self):
        return RetentionRow.read_many(self._value[self.RUNS])

    def receipt(self, completed_at):
        return {**self._value, self.COMPLETED_AT: completed_at}
