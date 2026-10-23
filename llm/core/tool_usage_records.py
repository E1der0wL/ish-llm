"""Shared logical Tool usage evidence; execution policy belongs to the runtime scope."""

from copy import deepcopy
from dataclasses import dataclass, fields

from llm.errors import CodedError
from .contracts import JsonValue
from .tool_codes import ToolErrorCode


class ToolUsageError(CodedError, ValueError):
    """Corrupt usage evidence, distinct from authorization or quota exhaustion."""

    code = ToolErrorCode.USAGE_CORRUPT


@dataclass(frozen=True, slots=True)
class ToolUsageRecord(JsonValue):
    INVALID_FIELDS = "Tool usage requires the complete current record"
    INVALID_COUNTERS = "Tool usage counters must be nonnegative integers"
    INVALID_RESERVATIONS = "Tool usage reservations must be lists of nonempty strings"
    DUPLICATE_INVOCATIONS = "Tool usage invocation identities must be unique"
    INVALID_COLLECTION = "Tool usage records must be a list"
    INITIAL_COUNT = 0

    calls: int
    completed: int
    revision: int
    pending_approvals: list[str]
    invocations: list[str]

    @classmethod
    def counter_fields(cls):
        return (cls.calls.__name__, cls.completed.__name__, cls.revision.__name__)

    @classmethod
    def collection_fields(cls):
        return (cls.pending_approvals.__name__, cls.invocations.__name__)

    def __post_init__(self):
        if any(type(getattr(self, key)) is not int or getattr(self, key) < self.INITIAL_COUNT
               for key in self.counter_fields()):
            raise ToolUsageError(self.INVALID_COUNTERS)
        for key in self.collection_fields():
            values = getattr(self, key)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise ToolUsageError(self.INVALID_RESERVATIONS)
        if len(set(self.invocations)) != len(self.invocations):
            raise ToolUsageError(self.DUPLICATE_INVOCATIONS)
        JsonValue.__post_init__(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ToolUsageError(cls.INVALID_FIELDS)
        return cls(**value)

    @classmethod
    def read_many(cls, values):
        if not isinstance(values, list):
            raise ToolUsageError(cls.INVALID_COLLECTION)
        return [cls.from_dict(value) for value in values]

    @classmethod
    def initial(cls):
        """Only a new logical scope starts here; stored records never acquire defaults."""
        return cls(calls=cls.INITIAL_COUNT, completed=cls.INITIAL_COUNT,
                   revision=cls.INITIAL_COUNT, pending_approvals=[], invocations=[])

    @property
    def order(self):
        return self.revision, self.calls


class ToolUsageCheckpoint:
    FIELD = "runtime_tool_usage"
    INVALID_RECORDS = "Tool usage requires checkpoint records"
    MISSING = "Tool usage missing from checkpoint record: {key}"

    @classmethod
    def attach(cls, payload, usage):
        return {**deepcopy(payload), cls.FIELD: ToolUsageRecord.from_dict(usage).to_dict()}

    @classmethod
    def read(cls, value, *, key=None):
        if not isinstance(value, dict) or cls.FIELD not in value:
            raise ToolUsageError(cls.MISSING.format(key=key))
        return ToolUsageRecord.from_dict(value[cls.FIELD])

    @classmethod
    def payload(cls, value):
        """Validated projection only; the original durable record retains its evidence."""
        cls.read(value)
        return {key: deepcopy(item) for key, item in value.items() if key != cls.FIELD}

    @classmethod
    def read_records(cls, records):
        if not isinstance(records, dict):
            raise ToolUsageError(cls.INVALID_RECORDS)
        return [cls.read(value, key=key).to_dict() for key, value in records.items()]
