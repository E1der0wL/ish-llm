"""Explicit MCP discovery and revisioned Agent selection via public owners."""

from copy import deepcopy
import hashlib
import re

from llm.core.agent_views import AgentSnapshot, AgentDescription
from llm.core.agents import AgentDefinition
from llm.core.editable_snapshot import EditableSnapshot
from llm.components.mcp.records import MCPCatalog, MCPToolSnapshot
from llm.core.schema import SchemaKeyword
from llm.services.infrastructure.storage import revision_token

from .record_management import RecordManagement
from .management_values import MCPOperation
from .mcp_views import MCPAgentChoice, MCPDiscovery, MCPSelection, MCPApplied, MCPToolPage
from .participants import AgentProfile
from ..execution.engine_selection import HubEngineIdentity
from ..progress import OperationProgress
from ...locales.keys import MessageKey


class MCPManagement(RecordManagement):
    ALIAS_PATTERN = r"[^A-Za-z0-9_-]"
    ALIAS_REPLACEMENT = "_"
    ALIAS_STEM = "mcp_{server}_{remote}"
    ALIAS_IDENTITY = "{server}/{remote}"
    ALIAS_MAX_STEM = 48
    ALIAS_DIGEST = 12
    ALIAS_JOIN = "{stem}_{suffix}"
    ALIAS_CONFLICT = "MCP alias conflicts with an existing Tool; edit Agent resources explicitly"
    DEFINITION_CHANGED = "MCP definition changed; reload before connecting"
    CONNECTION_CHANGED = "MCP definition changed during connection; select it again"
    DISCOVERY_CHANGED = "MCP discovery changed; connect and select again"
    TOOL_MISSING = "Selected MCP Tool is no longer available"
    AGENT_CHANGED = "Agent changed; reload its Tool selection before applying"

    def __init__(self, data, name, root, project):
        super().__init__(data, name, root)
        self.project = project

    @classmethod
    def _aliases(cls, server, selected, definition):
        resources = definition.get(AgentDefinition.Field.RESOURCES, {})
        servers = resources.get(AgentDefinition.ResourceField.MCP, {})
        previous = servers.get(server, {})
        aliases = {alias: remote for alias, remote in previous.items() if remote in selected}
        used = set(definition.get(AgentDefinition.Field.TOOLS, ()))
        used.update(alias for name, mapping in servers.items() if name != server for alias in mapping)
        for remote in selected:
            if remote in aliases.values():
                continue
            stem = re.sub(cls.ALIAS_PATTERN, cls.ALIAS_REPLACEMENT,
                          cls.ALIAS_STEM.format(server=server, remote=remote))[:cls.ALIAS_MAX_STEM]
            suffix = hashlib.sha256(cls.ALIAS_IDENTITY.format(server=server, remote=remote).encode()).hexdigest()[:cls.ALIAS_DIGEST]
            alias = cls.ALIAS_JOIN.format(stem=stem, suffix=suffix)
            if alias in used or alias in aliases:
                raise ValueError(cls.ALIAS_CONFLICT)
            aliases[alias] = remote
        return aliases

    async def discover(self, identifier, expected_version):
        # The selected definition must still be the one shown to the user before
        # starting a local server or contacting a remote endpoint.
        snapshot = EditableSnapshot.read(await self.data.asnapshot(identifier))
        if expected_version is None or snapshot.version != expected_version:
            raise ValueError(self.DEFINITION_CHANGED)
        OperationProgress.report(MessageKey.MCP_CONNECTING)
        catalog = MCPCatalog.from_dict(await self.data.aprepare(identifier))
        if catalog.version != snapshot.version:
            raise ValueError(self.CONNECTION_CHANGED)
        OperationProgress.report(MessageKey.MCP_SELECTING)
        agents, descriptions = [], {}
        for name in await self.project.agents.alist():
            if HubEngineIdentity.internal(name):
                continue
            agent = AgentSnapshot.read(await self.project.agents.asnapshot(name))
            engine = AgentDefinition.engine(agent.definition)
            if engine not in descriptions:
                descriptions[engine] = AgentDescription.from_dict(
                    await self.project.agents.adescribe(engine=engine)).schema
            schema = descriptions[engine]
            # Engine-owned schemas decide eligibility; Graph orchestrators do not
            # acquire model Tools through a guessed engine-name exception.
            properties = schema[SchemaKeyword.PROPERTIES]
            resources = properties.get(AgentDefinition.Field.RESOURCES, {}).get(SchemaKeyword.PROPERTIES, {})
            if AgentDefinition.ResourceField.MCP in resources:
                bindings = agent.definition.get(AgentDefinition.Field.RESOURCES, {}).get(
                    AgentDefinition.ResourceField.MCP, {}).get(identifier, {})
                agents.append(MCPAgentChoice(name, AgentProfile.title(name, agent.definition),
                                             bindings, agent.revision))
        tools_version = revision_token(MCPToolPage(catalog.tools).to_dict())
        return MCPDiscovery(identifier, catalog.tools, tuple(agents), snapshot.version, tools_version).to_dict()

    async def select_tools(self, value):
        selection = MCPSelection.from_dict(value)
        server, agent_id, names = selection.server, selection.agent, selection.tools
        # alist_tools checks definition + connector revisions without reconnecting.
        tools = tuple(MCPToolSnapshot.from_dict(tool) for tool in await self.data.alist_tools(server))
        definition = EditableSnapshot.read(await self.data.asnapshot(server))
        # Compare the public discovery view, never inspect backend catalog paths.
        if definition.version != selection.definition_version or revision_token(MCPToolPage(tools).to_dict()) != selection.tools_version:
            raise ValueError(self.DISCOVERY_CHANGED)
        if set(names) - {tool.name for tool in tools}:
            raise ValueError(self.TOOL_MISSING)
        agent = AgentSnapshot.read(await self.project.agents.asnapshot(agent_id))
        if agent.revision != selection.agent_revision:
            raise ValueError(self.AGENT_CHANGED)
        values = deepcopy(agent.definition)
        resources = values.setdefault(AgentDefinition.Field.RESOURCES, {})
        servers = resources.setdefault(AgentDefinition.ResourceField.MCP, {})
        aliases = self._aliases(server, names, values)
        if aliases:
            servers[server] = aliases
        else:
            servers.pop(server, None)
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_WRITE)
        await self.project.agents.arevise(agent_id, values,
                                         expected_revision=selection.agent_revision)
        return MCPApplied(agent_id, len(names)).to_dict()

    async def execute(self, action, argument="", expected_version=None):
        if action == MCPOperation.DISCOVER:
            return await self.discover(argument, expected_version)
        if action == MCPOperation.SELECT:
            return await self.select_tools(argument)
        return await super().execute(action, argument, expected_version)
