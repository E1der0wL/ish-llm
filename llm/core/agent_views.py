"""Agent definition observations, distinct from persisted Project envelopes."""

from copy import deepcopy
from dataclasses import dataclass

from .agents import AgentDefinition
from .contracts import JsonValue


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
