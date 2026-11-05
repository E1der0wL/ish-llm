"""Explicit MCP discovery and revisioned Agent selection via public owners."""

from copy import deepcopy
import hashlib
import re

from llm.core.agent_views import AgentSnapshot, AgentDescription
from llm.core.agents import AgentDefinition
from llm.core.editable_snapshot import EditableSnapshot
from llm.components.mcp.records import MCPCatalog
from llm.services.infrastructure.storage import revision_token

from .record_management import RecordManagement
from .management_values import MCPOperation
from .participants import AgentProfile
from ..execution.engine_selection import HubEngineIdentity
from ..progress import OperationProgress
from ...locales.keys import MessageKey


class MCPManagement(RecordManagement):
    def __init__(self, data, name, root, project):
        super().__init__(data, name, root)
        self.project = project

    @staticmethod
    def _aliases(server, selected, definition):
        resources = definition.get(AgentDefinition.Field.RESOURCES, {})
        servers = resources.get(AgentDefinition.ResourceField.MCP, {})
        previous = servers.get(server, {})
        aliases = {alias: remote for alias, remote in previous.items() if remote in selected}
        used = set(definition.get(AgentDefinition.Field.TOOLS, ()))
        used.update(alias for name, mapping in servers.items() if name != server for alias in mapping)
        for remote in selected:
            if remote in aliases.values():
                continue
            stem = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp_{server}_{remote}")[:48]
            suffix = hashlib.sha256(f"{server}/{remote}".encode()).hexdigest()[:12]
            alias = f"{stem}_{suffix}"
            if alias in used or alias in aliases:
                raise ValueError("MCP alias conflicts with an existing Tool; edit Agent resources explicitly")
            aliases[alias] = remote
        return aliases

    async def discover(self, identifier, expected_version):
        # The selected definition must still be the one shown to the user before
        # starting a local server or contacting a remote endpoint.
        snapshot = EditableSnapshot.read(await self.data.asnapshot(identifier))
        if expected_version is None or snapshot.version != expected_version:
            raise ValueError("MCP definition changed; reload before connecting")
        OperationProgress.report(MessageKey.MCP_CONNECTING)
        catalog = MCPCatalog.from_dict(await self.data.aprepare(identifier))
        if catalog.version != snapshot.version:
            raise ValueError("MCP definition changed during connection; select it again")
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
            properties = schema["properties"]
            resources = properties.get(AgentDefinition.Field.RESOURCES, {}).get("properties", {})
            if AgentDefinition.ResourceField.MCP in resources:
                bindings = agent.definition.get(AgentDefinition.Field.RESOURCES, {}).get(
                    AgentDefinition.ResourceField.MCP, {}).get(identifier, {})
                agents.append({"name": name, "label": AgentProfile.title(name, agent.definition),
                               "bindings": bindings, "revision": agent.revision})
        return {"server": identifier, "tools": catalog.tool_records(), "agents": agents}

    async def select_tools(self, value):
        server, agent_id, names = value["server"], value["agent"], value["tools"]
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
            raise ValueError("Select distinct MCP Tool names")
        # alist_tools checks definition + connector revisions without reconnecting.
        tools = await self.data.alist_tools(server)
        definition = EditableSnapshot.read(await self.data.asnapshot(server))
        # Compare the public discovery view, never inspect backend catalog paths.
        if definition.version != value["definition_version"] or revision_token({"tools": tools}) != value["tools_version"]:
            raise ValueError("MCP discovery changed; connect and select again")
        if set(names) - {tool["name"] for tool in tools}:
            raise ValueError("Selected MCP Tool is no longer available")
        agent = AgentSnapshot.read(await self.project.agents.asnapshot(agent_id))
        if agent.revision != value["agent_revision"]:
            raise ValueError("Agent changed; reload its Tool selection before applying")
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
                                         expected_revision=value["agent_revision"])
        return {"agent": agent_id, "tools": len(names)}

    async def execute(self, action, argument="", expected_version=None):
        if action == MCPOperation.DISCOVER:
            result = await self.discover(argument, expected_version)
            result["definition_version"] = expected_version
            result["tools_version"] = revision_token({"tools": result["tools"]})
            return result
        if action == MCPOperation.SELECT:
            return await self.select_tools(argument)
        return await super().execute(action, argument, expected_version)
