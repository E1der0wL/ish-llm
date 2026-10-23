"""Agent definition observations, distinct from persisted Project envelopes."""

from copy import deepcopy
from dataclasses import dataclass

from .agents import AgentDefinition
from .contracts import JsonValue
from .json_data import JsonObject


@dataclass(frozen=True, slots=True)
class AgentDefinitions(JsonValue):
    """Agent capability projection; selected definition validation stays with its domain."""

    UNIQUE_SELECTION = "{capability}/{identifier} must resolve to exactly one definition"
    SINGLE_DEFINITION = 1

    records: dict

    def __post_init__(self):
        JsonObject.validate(self.records)
        JsonValue.__post_init__(self)

    @classmethod
    def from_source(cls, source):
        return cls(source[cls.records.__name__])

    @classmethod
    def selected(cls, sources, identifier):
        candidates = [export.records[identifier] for source in sources
                      for export in (cls.from_source(source),) if identifier in export.records]
        if len(candidates) != cls.SINGLE_DEFINITION:
            raise ValueError(cls.UNIQUE_SELECTION.format(capability=AgentDefinition.CAPABILITY, identifier=identifier))
        return deepcopy(candidates[0])


@dataclass(frozen=True, slots=True)
class AgentDependency(JsonValue):
    """Identity and current definition revision in a dependent-resource view."""

    agent_id: str
    revision: str

    @classmethod
    def from_snapshot(cls, snapshot):
        return cls(snapshot.agent_id, snapshot.revision)


@dataclass(frozen=True, slots=True)
class AgentSnapshotFields(JsonValue):
    agent_id: str
    definition: dict
    revision: str


@dataclass(frozen=True, slots=True, init=False)
class AgentSnapshot:
    INVALID_REVISION = "Agent snapshot revision does not match its definition"

    _record: AgentSnapshotFields

    def __init__(self, agent_id, definition, revision):
        record = AgentSnapshotFields(agent_id, definition, revision)
        AgentDefinition.validate(record.agent_id, record.definition)
        if record.revision != AgentDefinition.revision(record.definition):
            raise ValueError(self.INVALID_REVISION)
        object.__setattr__(self, self.__class__._record.__name__, record)

    @classmethod
    def read(cls, value):
        record = AgentSnapshotFields.from_dict(value)
        return cls(record.agent_id, record.definition, record.revision)

    @classmethod
    def from_record(cls, record):
        return cls(record.id, record.definition, record.revision)

    @property
    def agent_id(self):
        return self._record.agent_id

    @property
    def definition(self):
        return deepcopy(self._record.definition)

    @property
    def revision(self):
        return self._record.revision

    def to_dict(self):
        return self._record.to_dict()
