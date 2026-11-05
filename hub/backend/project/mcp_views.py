"""Revision-bound MCP manager choices. Native catalogs remain the execution owner."""

from dataclasses import dataclass

from llm.core.contracts import JsonValue
from llm.components.mcp.records import MCPToolSnapshot


@dataclass(frozen=True, slots=True)
class MCPAgentChoice(JsonValue):
    name: str
    label: str
    bindings: dict[str, str]
    revision: str


@dataclass(frozen=True, slots=True)
class MCPDiscovery(JsonValue):
    server: str
    tools: tuple[MCPToolSnapshot, ...]
    agents: tuple[MCPAgentChoice, ...]
    definition_version: str
    tools_version: str

    def select(self, agent, tools):
        choice = next(item for item in self.agents if item.name == agent)
        return MCPSelection(self.server, self.definition_version, self.tools_version,
                            choice.name, choice.revision, tuple(tools))


@dataclass(frozen=True, slots=True)
class MCPSelection(JsonValue):
    DUPLICATE = "Select distinct MCP Tool names"

    server: str
    definition_version: str
    tools_version: str
    agent: str
    agent_revision: str
    tools: tuple[str, ...]

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if len(set(self.tools)) != len(self.tools):
            raise ValueError(self.DUPLICATE)


@dataclass(frozen=True, slots=True)
class MCPApplied(JsonValue):
    agent: str
    tools: int


@dataclass(frozen=True, slots=True)
class MCPToolPage(JsonValue):
    """Stable discovery fingerprint input; not a second MCP tool definition."""

    tools: tuple[MCPToolSnapshot, ...]
