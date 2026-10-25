"""Public Runtime response projections; no admission or execution authority."""

from dataclasses import dataclass
from llm.core.agents import AgentDefinition
from ..model import AgentSummary
from .execution.engine_selection import HubEngineIdentity


@dataclass(frozen=True, slots=True)
class RuntimeAgentCatalog:
    agents: dict

    @classmethod
    def from_definitions(cls, definitions):
        return cls(
            {
                identifier: AgentSummary(
                    identifier,
                    AgentDefinition.engine(definition),
                    AgentDefinition.purpose(definition),
                ).catalog_entry()
                for identifier, definition in definitions.items()
                if not HubEngineIdentity.internal(identifier)
            }
        )

    def to_dict(self):
        return {type(self).agents.__name__: self.agents}
