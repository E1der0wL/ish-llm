"""Durable selected Tool identity, recorded before approval or external effects."""

from copy import deepcopy
from dataclasses import dataclass, fields
from typing import Any

from .contracts import JsonValue
from .json_data import JsonObject
from .checkpoints import CheckpointStatus
from .tool_usage_records import ToolUsageCheckpoint


@dataclass(frozen=True, slots=True)
class _SelectionFields(JsonValue):
    tool: str
    arguments: dict
    binding: Any

    def __post_init__(self):
        # Validate original JSON syntax before a generic codec can turn tuples
        # or runtime values into apparently valid persisted evidence.
        JsonObject.validate({item.name: getattr(self, item.name) for item in fields(self)})
        JsonValue.__post_init__(self)


@dataclass(frozen=True, slots=True, init=False)
class ToolInvocationSelection:
    EMPTY_TOOL = "Selected Tool requires a nonempty name"

    _fields: _SelectionFields

    def __init__(self, tool, arguments, binding):
        value = _SelectionFields(tool, arguments, binding)
        if not value.tool:
            raise ValueError(self.EMPTY_TOOL)
        object.__setattr__(self, self.__class__._fields.__name__, value)

    @classmethod
    def read(cls, value):
        JsonObject.validate(value)
        record = _SelectionFields.from_dict(value)
        return cls(record.tool, record.arguments, record.binding)

    @property
    def tool(self):
        return self._fields.tool

    @property
    def arguments(self):
        return deepcopy(self._fields.arguments)

    @property
    def binding(self):
        return deepcopy(self._fields.binding)

    def to_dict(self):
        return self._fields.to_dict()


@dataclass(frozen=True, slots=True)
class _BindingRecordFields(JsonValue):
    node_type: str
    status: CheckpointStatus
    container: bool
    selection: dict
    request_key: str
    checkpoint_name: str


@dataclass(frozen=True, slots=True, init=False)
class ToolBindingRecord:
    KIND = "tool_binding"
    KEY_PREFIX = "@tool-binding:"
    CONTAINER = True
    INVALID_RECORD = "Invalid selected Tool binding record"
    INVALID_IDENTITY = "Selected Tool binding requires request and checkpoint identities"
    MISMATCHED_IDENTITY = "Selected Tool binding identity does not match its checkpoint"

    _fields: _BindingRecordFields

    @classmethod
    def create(cls, selection, request_key, checkpoint_name):
        value = _BindingRecordFields(cls.KIND, CheckpointStatus.COMPLETED, cls.CONTAINER,
                                     selection.to_dict(), request_key, checkpoint_name)
        return cls.read(value.to_dict())

    @classmethod
    def is_entry(cls, key, value):
        # A missing kind cannot hide a record in this reserved key namespace.
        return key.startswith(cls.KEY_PREFIX) or value.get(_BindingRecordFields.node_type.__name__) == cls.KIND

    @classmethod
    def key_for(cls, request_key):
        if not isinstance(request_key, str) or not request_key:
            raise ValueError(cls.INVALID_IDENTITY)
        return cls.KEY_PREFIX + request_key

    @classmethod
    def read(cls, value):
        JsonObject.validate(value)
        record = _BindingRecordFields.from_dict(value)
        if (record.node_type != cls.KIND or record.status != CheckpointStatus.COMPLETED
                or record.container is not cls.CONTAINER):
            raise ValueError(cls.INVALID_RECORD)
        if not record.request_key or not record.checkpoint_name:
            raise ValueError(cls.INVALID_IDENTITY)
        ToolInvocationSelection.read(record.selection)
        result = object.__new__(cls)
        object.__setattr__(result, cls._fields.__name__, record)
        return result

    @classmethod
    def from_checkpoint(cls, value):
        return cls.read(ToolUsageCheckpoint.payload(value))

    @property
    def selection(self):
        return ToolInvocationSelection.read(self._fields.selection)

    @property
    def request_key(self):
        return self._fields.request_key

    @property
    def checkpoint_name(self):
        return self._fields.checkpoint_name

    def require_key(self, key):
        if key != self.key_for(self.request_key):
            raise ValueError(self.MISMATCHED_IDENTITY)

    def require_checkpoint(self, name):
        if name != self.checkpoint_name:
            raise ValueError(self.MISMATCHED_IDENTITY)

    def to_dict(self):
        return self._fields.to_dict()
