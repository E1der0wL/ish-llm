"""Memory revision history and envelope validation; no filesystem or execution authority."""

from copy import deepcopy
from .records import MemoryRecord


class MemoryHistory:
    CREATE = "create"
    SAVE = "save"
    UPDATE = "update"
    DELETE = "delete"
    RESTORE = "restore"
    OPERATIONS = (CREATE, SAVE, UPDATE, DELETE, RESTORE)
    ABSENT = None
    FIRST_REVISION = MemoryRecord.MINIMUM_REVISION
    LAST_ENTRY = -1
    PRIOR_ENTRY = -2
    MINIMUM_TRANSITION_ENTRIES = 2
    INVALID_CREATION = "Memory history requires its original creation"
    CHANGED_IDENTITY = "Memory history changes immutable identity or scope"
    INVALID_TRANSITION = "Invalid memory history state transition"
    CHANGED_CONTENT = "Memory deletion or restoration changed stored content"
    INVALID_ENVELOPE = "Memory envelope requires only record/history"
    MISSING_HISTORY = "Memory requires a record and revision history"
    INCOMPLETE_HISTORY = "Incomplete memory revision history"
    INVALID_ENTRY = "Invalid memory history entry"
    INVALID_FIELDS = "Invalid Memory history fields"
    CROSSED_GENERATION = "Memory history crosses record generations"
    INVALID_REVISION = "Invalid memory revision history"
    DISAGREEMENT = "Memory and history disagree"

    class Field:
        REVISION = MemoryRecord.Field.REVISION
        OPERATION = "operation"
        AT = "at"
        SOURCE = MemoryRecord.Field.SOURCE
        DATA = "data"

    FIELDS = frozenset((Field.REVISION, Field.OPERATION, Field.AT, Field.SOURCE, Field.DATA))

    @classmethod
    def entry(cls, *, revision, operation, at, source, data):
        return {cls.Field.REVISION: revision, cls.Field.OPERATION: operation,
                cls.Field.AT: at, cls.Field.SOURCE: source, cls.Field.DATA: data}

    @classmethod
    def validate_transition(cls, previous, entry, *, create, delete, restore, identity_fields):
        record, operation = entry[cls.Field.DATA], entry[cls.Field.OPERATION]
        if previous is cls.ABSENT:
            if (operation != create or record[MemoryRecord.Field.DELETED]
                    or record[MemoryRecord.Field.CREATED_AT] != record[MemoryRecord.Field.UPDATED_AT] or entry[cls.Field.SOURCE] != record[cls.Field.SOURCE]):
                raise ValueError(cls.INVALID_CREATION)
            return
        if any(record.get(key) != previous.get(key) for key in identity_fields):
            raise ValueError(cls.CHANGED_IDENTITY)
        if (operation == create or previous[MemoryRecord.Field.DELETED] != (operation == restore)
                or record[MemoryRecord.Field.DELETED] != (operation == delete)):
            raise ValueError(cls.INVALID_TRANSITION)
        if operation in (delete, restore):
            expected = {**previous, cls.Field.REVISION: record[cls.Field.REVISION], MemoryRecord.Field.UPDATED_AT: record[MemoryRecord.Field.UPDATED_AT],
                        MemoryRecord.Field.DELETED: record[MemoryRecord.Field.DELETED]}
            if operation == delete and record.get(MemoryRecord.Field.METADATA) != previous.get(MemoryRecord.Field.METADATA):
                from .consolidation import MemoryConsolidation
                expected[MemoryRecord.Field.METADATA] = MemoryConsolidation.deletion_metadata(previous, record, entry[cls.Field.SOURCE])
            if record != expected:
                raise ValueError(cls.CHANGED_CONTENT)

    @classmethod
    def validate_envelope(cls, identifier, data, *, validate_memory, validate_transition, operations, fields):
        if data.keys() - {MemoryEnvelope.Field.RECORD, MemoryEnvelope.Field.HISTORY}:
            raise ValueError(cls.INVALID_ENVELOPE)
        record, history = data.get(MemoryEnvelope.Field.RECORD), data.get(MemoryEnvelope.Field.HISTORY)
        if not isinstance(record, dict) or not isinstance(history, list) or not history:
            raise ValueError(cls.MISSING_HISTORY)
        validate_memory(identifier, record)
        if len(history) != record[cls.Field.REVISION]:
            raise ValueError(cls.INCOMPLETE_HISTORY)
        previous = cls.ABSENT
        for revision, entry in enumerate(history, cls.FIRST_REVISION):
            if not isinstance(entry, dict) or not isinstance(entry.get(cls.Field.DATA), dict):
                raise ValueError(cls.INVALID_ENTRY)
            if entry.keys() != fields:
                raise ValueError(cls.INVALID_FIELDS)
            validate_memory(identifier, entry[cls.Field.DATA])
            if entry[cls.Field.DATA][MemoryRecord.Field.GENERATION] != record[MemoryRecord.Field.GENERATION]:
                raise ValueError(cls.CROSSED_GENERATION)
            if type(entry[cls.Field.REVISION]) is not int or entry[cls.Field.REVISION] != revision or entry[cls.Field.DATA][cls.Field.REVISION] != revision or (
                    entry[cls.Field.OPERATION] not in operations
                    or not isinstance(entry[cls.Field.SOURCE], dict) or entry[cls.Field.AT] != entry[cls.Field.DATA][MemoryRecord.Field.UPDATED_AT]):
                raise ValueError(cls.INVALID_REVISION)
            validate_transition(previous, entry)
            previous = entry[cls.Field.DATA]
        if history[cls.LAST_ENTRY][cls.Field.DATA] != record:
            raise ValueError(cls.DISAGREEMENT)

    @classmethod
    def optional_source(cls, entry):
        return entry.get(cls.Field.SOURCE)


class MemoryEnvelope:
    class Field:
        RECORD = "record"
        HISTORY = "history"

    def __init__(self, value):
        self._value = value

    @classmethod
    def append(cls, record, history, entry):
        return {cls.Field.RECORD: record, cls.Field.HISTORY: [*history, entry]}

    @property
    def record(self):
        return self._value[self.Field.RECORD]

    @property
    def history(self):
        return self._value[self.Field.HISTORY]

    @classmethod
    def create(cls, record, history):
        return {cls.Field.RECORD: record, cls.Field.HISTORY: history}

    def previous(self):
        return self.create(deepcopy(self.history[MemoryHistory.PRIOR_ENTRY][MemoryHistory.Field.DATA]),
                           deepcopy(self.history[:MemoryHistory.LAST_ENTRY]))
