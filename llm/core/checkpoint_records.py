"""Checkpoint storage records and common projections; Engine payload stays opaque."""

from copy import deepcopy
from dataclasses import dataclass, fields, replace
from enum import StrEnum
import re
from typing import Any, ClassVar

from .contracts import JsonValue
from .configuration import UNSET
from .models import Run, RunStatus
from llm.core.json_data import JsonObject
from .checkpoints import ResumeAbandonment, CheckpointOperation
from .interactions import InteractionRequest


class CheckpointRevision:
    PATTERN = r"[a-f0-9]{64}"
    INVALID = "Invalid checkpoint revision"

    @classmethod
    def validate(cls, value):
        if not isinstance(value, str) or not re.fullmatch(cls.PATTERN, value):
            raise ValueError(cls.INVALID)


@dataclass(frozen=True, slots=True)
class CheckpointRecords(JsonValue):
    INVALID: ClassVar[str] = "Checkpoint requires object records"
    INVALID_ENTRY: ClassVar[str] = "Checkpoint record requires a nonempty key and object value"
    records: dict

    def __post_init__(self):
        if not isinstance(self.records, dict):
            raise ValueError(self.INVALID)
        for key, value in self.records.items():
            self.validate_entry(key, value)
        JsonValue.__post_init__(self)

    @classmethod
    def validate_entry(cls, key, value):
        if not isinstance(key, str) or not key or not isinstance(value, dict):
            raise ValueError(cls.INVALID_ENTRY)
        JsonObject.validate(value)

    @classmethod
    def from_checkpoint(cls, value):
        """Read the common record projection; its source Owner validates storage identity."""
        return cls(**{item.name: value[item.name] for item in fields(cls)})

    @classmethod
    def replace_records(cls, checkpoint, records):
        """Replace the common projection on a copy; retain all envelope/header fields."""
        cls.from_checkpoint(checkpoint)
        value = cls(records)
        return {**deepcopy(checkpoint), cls.records.__name__: value.records}

    @classmethod
    def records_or_empty(cls, checkpoint):
        """Only an absent checkpoint denotes fresh execution; missing fields are corrupt."""
        return {} if checkpoint is None else cls.from_checkpoint(checkpoint).records


@dataclass(frozen=True, slots=True)
class CheckpointContent(CheckpointRecords):
    INVALID: ClassVar[str] = "Checkpoint requires object header and records"
    header: dict

    def __post_init__(self):
        if not isinstance(self.header, dict):
            raise ValueError(self.INVALID)
        CheckpointRecords.__post_init__(self)

    @classmethod
    def engine_snapshot(cls, *, header, records):
        """Current runtime content projection, preserving the Engine wire field order."""
        value = cls(header=header, records=records)
        return {cls.header.__name__: deepcopy(value.header), cls.records.__name__: deepcopy(value.records)}


@dataclass(frozen=True, slots=True)
class CheckpointSnapshot(CheckpointContent):
    SCHEMA_VERSION: ClassVar[int] = 1
    INVALID_FIELDS: ClassVar[str] = "Checkpoint requires all current storage fields"
    INVALID_OWNER: ClassVar[str] = "Checkpoint version or Run ownership mismatch"
    schema_version: int
    run_id: str
    engine: str

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(self.INVALID_OWNER)
        CheckpointContent.__post_init__(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        return cls(**deepcopy(value))

    def require_owner(self, run):
        if self.run_id != run.id or self.engine != run.engine:
            raise ValueError(self.INVALID_OWNER)


@dataclass(frozen=True, slots=True)
class CheckpointManifest(CheckpointSnapshot):
    INVALID_REVISIONS: ClassVar[str] = "Checkpoint requires object header, records and revisions"
    INVALID_REVISION: ClassVar[str] = "Invalid checkpoint record revision"
    record_revisions: dict

    def __post_init__(self):
        if not isinstance(self.record_revisions, dict):
            raise ValueError(self.INVALID_REVISIONS)
        for key, revision in self.record_revisions.items():
            if not isinstance(key, str) or not key:
                raise ValueError(self.INVALID_REVISION)
            CheckpointRevision.validate(revision)
        CheckpointSnapshot.__post_init__(self)

    @classmethod
    def initialize(cls, run, header, records):
        return cls(header=header, records=records, schema_version=cls.SCHEMA_VERSION,
                   run_id=run.id, engine=run.engine, record_revisions={})

    def with_revision(self, key, revision):
        return replace(self, record_revisions={**self.record_revisions, key: revision})

    def resolved(self, records):
        return CheckpointSnapshot(header=self.header, records=records, schema_version=self.schema_version,
                                  run_id=self.run_id, engine=self.engine)


@dataclass(frozen=True, slots=True)
class CheckpointRecord(JsonValue):
    INVALID: ClassVar[str] = "Checkpoint record identity or revision mismatch"
    key: str
    value: dict

    def __post_init__(self):
        CheckpointContent.validate_entry(self.key, self.value)
        JsonValue.__post_init__(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID)
        return cls(**deepcopy(value))


@dataclass(frozen=True, slots=True)
class CheckpointInteractions:
    """An explicit name and common records, never a replacement storage Reader."""

    name: str
    records: dict

    @classmethod
    def from_snapshot(cls, value, name):
        snapshot = CheckpointSnapshot.from_dict(value)
        return cls(name, deepcopy(snapshot.records))


@dataclass(frozen=True, slots=True)
class CheckpointDeclarations:
    NAMES: ClassVar[str] = "checkpoints"
    REVISIONS: ClassVar[str] = "checkpoint_revisions"
    INVALID: ClassVar[str] = "Invalid checkpoint declarations"
    MISSING_WITNESS: ClassVar[str] = "Checkpoint declaration requires its revision witness"
    names: tuple[str, ...]
    revisions: dict

    @classmethod
    def read(cls, metadata):
        from llm.components.base import validate_name
        names, revisions = metadata.get(cls.NAMES, []), metadata.get(cls.REVISIONS, {})
        if (not isinstance(names, list) or len(set(names)) != len(names)
                or not isinstance(revisions, dict) or set(names) != set(revisions)):
            raise ValueError(cls.INVALID)
        for name in names:
            validate_name(name)
            CheckpointRevision.validate(revisions[name])
        if (cls.NAMES in metadata) != (cls.REVISIONS in metadata):
            raise ValueError(cls.MISSING_WITNESS)
        return cls(tuple(names), deepcopy(revisions))

    @classmethod
    def reference_names(cls, metadata):
        from .continuations import ResumeRecord
        names = cls.read(metadata).names
        if names or CheckpointDeletion.read(metadata) is not None:
            return names
        inherited = ResumeRecord.checkpoint_name(metadata)
        return () if inherited is None else (inherited,)

    @classmethod
    def record_revision(cls, metadata, name, revision):
        metadata.setdefault(cls.REVISIONS, {})[name] = revision

    @classmethod
    def declare(cls, metadata, name):
        metadata.setdefault(cls.NAMES, []).append(name)

    @classmethod
    def remove(cls, metadata):
        metadata.pop(cls.NAMES, None)
        metadata.pop(cls.REVISIONS, None)


@dataclass(frozen=True, slots=True)
class CheckpointDeletion(JsonValue):
    KEY: ClassVar[str] = "checkpoint_deletion"
    INVALID: ClassVar[str] = "Invalid checkpoint deletion record"
    DUPLICATE_NAMES: ClassVar[str] = "Duplicate deleted checkpoint names"
    DUPLICATE_INTERACTIONS: ClassVar[str] = "Duplicate archived interaction identity"
    INVALID_BINDING: ClassVar[str] = "Archived interaction checkpoint mismatch"
    RETIRED_STATES: ClassVar[tuple] = (RunStatus.COMPLETED, RunStatus.CANCELLED)
    deleted_at: str
    revision: str
    names: tuple[str, ...]
    interactions: tuple[InteractionRequest, ...]

    def __post_init__(self):
        from llm.components.base import validate_name
        if (not isinstance(self.deleted_at, str) or not self.deleted_at
                or not isinstance(self.revision, str) or not self.revision or not self.names):
            raise ValueError(self.INVALID)
        for name in self.names:
            validate_name(name)
        if len(set(self.names)) != len(self.names):
            raise ValueError(self.DUPLICATE_NAMES)
        if len({request.id for request in self.interactions}) != len(self.interactions):
            raise ValueError(self.DUPLICATE_INTERACTIONS)
        if any(request.checkpoint_binding.checkpoint not in self.names for request in self.interactions):
            raise ValueError(self.INVALID_BINDING)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID)
        values = deepcopy(value)
        names, interactions = cls.names.__name__, cls.interactions.__name__
        if not isinstance(values[names], list) or not isinstance(values[interactions], list):
            raise ValueError(cls.INVALID)
        values[names] = tuple(values[names])
        values[interactions] = tuple(InteractionRequest.from_dict(raw) for raw in values[interactions])
        return cls(**values)

    def require_retired(self, run):
        if CheckpointDeclarations.read(run.metadata).names or run.status in Run.ACTIVE_STATES:
            raise ValueError(self.INVALID)
        if run.status not in self.RETIRED_STATES:
            ResumeAbandonment.require(run.metadata)

    @classmethod
    def read(cls, metadata):
        value = metadata.get(cls.KEY)
        return None if value is None else cls.from_dict(value)

    def store(self, metadata):
        metadata[self.KEY] = self.to_dict()


class CheckpointWrite:
    """Engine write event metadata; initialization alone can supply empty records."""

    class Field(StrEnum):
        NAME = "name"
        OPERATION = "operation"
        HEADER = "header"
        RECORDS = "records"
        KEY = "key"
        VALUE = "value"
        LIFECYCLE = "lifecycle"

    def __init__(self, value):
        JsonObject.validate(value)
        self._value = deepcopy(value)

    @classmethod
    def record_event(cls, name, key, value):
        return cls({cls.Field.NAME: name, cls.Field.OPERATION: CheckpointOperation.RECORD,
                    cls.Field.KEY: key, cls.Field.VALUE: value})

    @classmethod
    def initialize_event(cls, name, header, records=UNSET):
        value = {cls.Field.NAME: name, cls.Field.OPERATION: CheckpointOperation.INITIALIZE,
                 cls.Field.HEADER: header}
        if records is not UNSET:
            value[cls.Field.RECORDS] = records
        return cls(value)

    def to_dict(self):
        return deepcopy(self._value)

    def with_record(self, value):
        return type(self)({**self._value, self.Field.VALUE: value})

    @property
    def name(self):
        return self._value[self.Field.NAME]

    @property
    def name_or_none(self):
        return self._value.get(self.Field.NAME)

    @property
    def operation(self):
        return self._value[self.Field.OPERATION]

    @property
    def initialize(self):
        return self.operation == CheckpointOperation.INITIALIZE

    @property
    def is_record(self):
        return self._value.get(self.Field.OPERATION) == CheckpointOperation.RECORD

    @property
    def lifecycle(self):
        return deepcopy(self._value.get(self.Field.LIFECYCLE))

    @property
    def initial_header(self):
        return deepcopy(self._value[self.Field.HEADER])

    @property
    def initial_records(self):
        return deepcopy(self._value.get(self.Field.RECORDS, {}))

    def initial_manifest(self, run):
        return CheckpointManifest.initialize(run, self.initial_header, self.initial_records)

    @property
    def record(self):
        return CheckpointRecord(self._value[self.Field.KEY], self._value[self.Field.VALUE])

    def interactions(self):
        if self.initialize:
            return CheckpointInteractions(self.name, self.initial_records)
        entry = self.record
        return CheckpointInteractions(self.name, {entry.key: entry.value})


@dataclass(frozen=True, slots=True)
class CheckpointWaitingObservation:
    """Display projection for nested waiting records; never an approval decision."""

    approval_required: bool = True
    name: Any = None
    arguments: Any = None
    prompt: Any = None

    @classmethod
    def from_payload(cls, payload):
        return cls(**{item.name: deepcopy(payload.get(item.name, item.default))
                      for item in fields(cls) if item.name != cls.approval_required.__name__})

    def to_dict(self):
        return {item.name: deepcopy(getattr(self, item.name)) for item in fields(self)}


class CheckpointStep:
    """Common lifecycle and approval evidence; Engine-private fields stay opaque."""

    class Field(StrEnum):
        STATUS = "status"
        INTERACTION = "interaction"
        REQUIRES_RETRY = "requires_retry"

    def __init__(self, value):
        self._value = deepcopy(value)

    @property
    def status(self):
        return self._value.get(self.Field.STATUS)

    @property
    def requires_retry(self):
        return bool(self._value.get(self.Field.REQUIRES_RETRY))

    @property
    def has_interaction(self):
        return self.Field.INTERACTION in self._value

    @property
    def interaction(self):
        return InteractionRequest.from_dict(self._value[self.Field.INTERACTION])

    @property
    def optional_interaction(self):
        raw = self._value.get(self.Field.INTERACTION)
        return None if raw is None else InteractionRequest.from_dict(raw)

    def with_waiting_observation(self, envelope):
        return {**deepcopy(envelope), **CheckpointWaitingObservation.from_payload(self._value).to_dict()}

    @classmethod
    def attach_interaction(cls, value, request):
        return {**deepcopy(value), cls.Field.INTERACTION.value: request.to_dict()}
