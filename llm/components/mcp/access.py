"""An explicitly opened MCP connection; no automatic input injection or replay."""

from copy import deepcopy
from llm.core.configuration import UNSET
from llm.components.tools.registry import Tool, ToolRegistry
from .client import MCPClientOperation
from .records import MCPToolSnapshot


class MCPAccess:
    """Application-owned connection lifetime; do not keep it in persistent state.

    Multiple resource operations and selected Tools may share this connection.
    Tools still run through ToolExecutor. Exiting closes access, not a request to
    retry outstanding or uncertain effects. Methods recheck Project ownership.
    """

    def __init__(self, data, identifier, connection, client):
        self._data, self._identifier = data, identifier
        self._connection, self._client = connection, client
        self._active = True

    def close(self):
        self._active = False

    async def _check(self):
        if not self._active:
            raise ValueError("MCP connection scope is closed")
        current = await self._data._async_call(self._data._connection, self._identifier)
        if (current.version, current.revision) != (self._connection.version, self._connection.revision):
            raise ValueError("MCP connection definition changed; open a new connection")

    async def _call(self, name, *args, **kwargs):
        await self._check()
        value = MCPClientOperation.require(self._client, name)(*args, **kwargs)
        from inspect import isawaitable
        if isawaitable(value):
            value = await value
        await self._check()
        return deepcopy(value)

    async def aget_status(self):
        return await self._call(MCPClientOperation.STATUS)

    async def alist_resources(self, *, cursor=UNSET):
        return await self._call(MCPClientOperation.RESOURCES, cursor=cursor)

    async def alist_templates(self, *, cursor=UNSET):
        return await self._call(MCPClientOperation.TEMPLATES, cursor=cursor)

    async def aread_resource(self, uri):
        return await self._call(MCPClientOperation.READ, uri)

    async def aprepare(self):
        await self._check()
        refresh = getattr(self._client, MCPClientOperation.REFRESH, None)
        tools = await refresh() if callable(refresh) else self._client
        if not isinstance(tools, ToolRegistry):
            raise TypeError("MCP discovery must provide ToolRegistry")
        await self._check()
        descriptors = [MCPToolSnapshot.from_tool(tools.get(name)) for name in tools.names()]
        return await self._data._async_call(self._data._publish_catalog, self._identifier,
            self._connection.version, self._connection.revision, descriptors)

    async def aget_tools(self, aliases):
        """Explicitly select aliases; returned handlers expire with this scope."""
        from .data import RemoteTool
        if not isinstance(aliases, dict) or any(not isinstance(v, str) for v in aliases.values()):
            raise ValueError("MCP aliases must map local names to remote Tool names")
        await self._check()
        snapshot = await self._data._async_call(self._data._catalog, self._identifier)
        await self._check()
        selected = ToolRegistry()
        for alias, name in aliases.items():
            descriptor = snapshot.selected(name)
            if descriptor is None:
                raise ValueError("MCP Tool is not in the prepared catalog")
            selected.register(Tool(alias, descriptor.description, descriptor.parameters,
                RemoteTool(self._data, self._identifier, snapshot, descriptor, access=self),
                contract=descriptor.execution_contract, classification=descriptor.execution_classification))
        return selected

    async def _get_tool(self, descriptor):
        await self._check()
        refresh = getattr(self._client, MCPClientOperation.REFRESH, None)
        tools = await refresh() if callable(refresh) else self._client
        if not isinstance(tools, ToolRegistry):
            raise TypeError("MCP discovery must provide ToolRegistry")
        tool = tools.get(descriptor.name)
        if MCPToolSnapshot.from_tool(tool) != descriptor:
            from llm.services.runtime.tools import ToolExecutionError
            raise ToolExecutionError("MCP Tool contract changed; prepare and submit again",
                                     effect=ToolExecutionError.NO_EFFECT)
        await self._check()
        return tool
