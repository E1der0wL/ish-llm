"""Run continuation metadata declarations and detached request projections.

These links describe stored lineage. Only the runtime can admit a continuation,
using the current checkpoint, approval, execution binding and budget evidence.
"""

from copy import deepcopy
from dataclasses import dataclass

from .configuration import UNSET
from .contracts import JsonValue
from llm.core.json_data import JsonObject


@dataclass(frozen=True, init=False, slots=True)
class RunLink:
    """One validated continuation value; projections never supply lost evidence."""

    SOURCE_RUN = "run_id"
    DIGEST = "digest"
    ABSENT = None
    INVALID_RECORD = "Continuation requires its original current fields"
    INVALID_IDENTITY = "Continuation identity and digest must be nonempty text"

    _value: dict

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or not set(cls.REQUIRED_FIELDS) <= value.keys():
            raise ValueError(cls.INVALID_RECORD)
        JsonObject.validate(value)
        for name in cls.TEXT_FIELDS:
            if not isinstance(value[name], str) or not value[name]:
                raise ValueError(cls.INVALID_IDENTITY)
        cls.validate_options(value)
        result = object.__new__(cls)
        object.__setattr__(result, RunLink._value.__name__, deepcopy(value))
        return result

    @classmethod
    def validate_options(cls, value):
        """Subclasses validate only their own additional contract fields."""

    @property
    def source_run_id(self):
        return self._value[self.SOURCE_RUN]

    @property
    def digest(self):
        return self._value[self.DIGEST]

    def to_dict(self):
        return deepcopy(self._value)

    def metadata(self):
        return {self.KEY: self.to_dict()}

    def store(self, metadata):
        metadata.update(self.metadata())

    @classmethod
    def present(cls, metadata):
        return cls.KEY in metadata

    @classmethod
    def from_metadata(cls, metadata):
        return cls.read(metadata[cls.KEY]) if cls.present(metadata) else cls.ABSENT

    @classmethod
    def require(cls, metadata):
        return cls.read(metadata[cls.KEY])

    @classmethod
    def source_id(cls, metadata):
        """Read the optional lineage projection without manufacturing a receipt."""
        value = cls.from_metadata(metadata)
        return value.source_run_id if value is not cls.ABSENT else cls.ABSENT

    @classmethod
    def require_source_id(cls, metadata):
        """Read an existing lineage witness without an absent-field fallback."""
        return cls.require(metadata).source_run_id

    @classmethod
    def remove(cls, metadata):
        """Used on detached clone context, never to delete execution evidence."""
        metadata.pop(cls.KEY, None)


class ResumeRecord(RunLink):
    __slots__ = ()

    KEY = "resume"
    CHECKPOINT = "checkpoint"
    RETRY_NODES = "retry_nodes"
    DECISIONS = "decisions"
    PARTIAL_OUTPUT = "partial_output"
    DEFAULT_PARTIAL_OUTPUT = False
    INCLUDE_PARTIAL_OUTPUT = True
    REQUIRED_FIELDS = (RunLink.SOURCE_RUN, CHECKPOINT, RunLink.DIGEST, RETRY_NODES, DECISIONS)
    TEXT_FIELDS = (RunLink.SOURCE_RUN, CHECKPOINT, RunLink.DIGEST)
    INVALID_CHOICES = "Resume requires its original retry nodes and decisions"
    INVALID_PARTIAL_OUTPUT = "partial_output must be a boolean"
    FRESH_RETRY_NODES = ()

    @classmethod
    def validate_options(cls, value):
        if (not isinstance(value[cls.RETRY_NODES], list)
                or any(not isinstance(key, str) for key in value[cls.RETRY_NODES])
                or not isinstance(value[cls.DECISIONS], dict)):
            raise ValueError(cls.INVALID_CHOICES)
        if cls.PARTIAL_OUTPUT in value and type(value[cls.PARTIAL_OUTPUT]) is not bool:
            raise ValueError(cls.INVALID_PARTIAL_OUTPUT)

    @property
    def checkpoint(self):
        return self._value[self.CHECKPOINT]

    @property
    def retry_nodes(self):
        return tuple(self._value[self.RETRY_NODES])

    @property
    def decisions(self):
        return deepcopy(self._value[self.DECISIONS])

    @property
    def partial_output(self):
        return self._value.get(self.PARTIAL_OUTPUT, self.DEFAULT_PARTIAL_OUTPUT)

    @classmethod
    def checkpoint_name(cls, metadata):
        value = cls.from_metadata(metadata)
        return value.checkpoint if value is not cls.ABSENT else cls.ABSENT

    @classmethod
    def decisions_for(cls, metadata):
        value = cls.from_metadata(metadata)
        return value.decisions if value is not cls.ABSENT else {}

    @classmethod
    def retry_nodes_for(cls, metadata):
        value = cls.from_metadata(metadata)
        return value.retry_nodes if value is not cls.ABSENT else cls.FRESH_RETRY_NODES

    @classmethod
    def create(cls, source_id, checkpoint, digest, retry_nodes, decisions, *, partial_output=DEFAULT_PARTIAL_OUTPUT):
        if type(partial_output) is not bool:
            raise ValueError(cls.INVALID_PARTIAL_OUTPUT)
        value = {cls.SOURCE_RUN: source_id, cls.CHECKPOINT: checkpoint, cls.DIGEST: digest,
                 cls.RETRY_NODES: list(retry_nodes), cls.DECISIONS: deepcopy(decisions)}
        if partial_output:
            value[cls.PARTIAL_OUTPUT] = cls.INCLUDE_PARTIAL_OUTPUT
        return cls.read(value)

    def for_child(self, *, retry_nodes, decisions):
        """Project already-validated parent decisions into an Engine-local scope."""
        return type(self).read({**self.to_dict(), self.DECISIONS: deepcopy(decisions), self.RETRY_NODES: list(retry_nodes)})


class RestartRecord(RunLink):
    __slots__ = ()

    KEY = "restart"
    REQUIRED_FIELDS = (RunLink.SOURCE_RUN, RunLink.DIGEST)
    TEXT_FIELDS = REQUIRED_FIELDS

    @classmethod
    def create(cls, source_id, digest):
        return cls.read({cls.SOURCE_RUN: source_id, cls.DIGEST: digest})


class RunExecutionEvidence:
    """Service-persisted dispatch entry; absence never proves no execution."""

    KEY = "execution_started"
    NOT_STARTED = False
    STARTED = True
    UNKNOWN = UNSET
    INVALID_VALUE = "Run execution entry evidence must be a boolean when present"

    @classmethod
    def read(cls, metadata):
        if cls.KEY not in metadata:
            return cls.UNKNOWN
        value = metadata[cls.KEY]
        if type(value) is not bool:
            raise ValueError(cls.INVALID_VALUE)
        return value

    @classmethod
    def never_started(cls, metadata):
        return cls.read(metadata) is cls.NOT_STARTED

    @classmethod
    def admission_metadata(cls):
        return {cls.KEY: cls.NOT_STARTED}

    @classmethod
    def mark_started(cls, metadata):
        metadata[cls.KEY] = cls.STARTED


@dataclass(frozen=True, slots=True)
class RestartEvidence(JsonValue):
    """Hash input only; full Run and Message records keep their original owners."""

    run: dict
    input: dict


@dataclass(frozen=True, slots=True)
class ResumePlanEvidence(JsonValue):
    """Detached hash input, never an approval or a reconstructed execution record."""

    run: dict
    checkpoint: dict | None
    views: tuple[dict, ...]
    project: dict
    session: dict
    messages: tuple[dict, ...]
    runs: tuple[dict, ...]
