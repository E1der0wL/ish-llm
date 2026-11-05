"""Live MCP SDK session operations; lifetime is the explicit connector context."""

import asyncio
from copy import deepcopy
from contextlib import asynccontextmanager
from uuid import uuid4
from llm.components.tools.registry import ToolRegistry
from llm.core.configuration import UNSET
from .mcp_interactions import MCPElicitation
from .mcp import MCPConnectionState
from llm.services.infrastructure.cancellation import cancel_tasks


class MCPSession(ToolRegistry):
    """ToolRegistry public contract plus optional resource/status operations."""

    def __init__(self, connector, session, initialized, *, connection_id):
        super().__init__()
        self.connector, self.session = connector, session
        self.connection_id = connection_id
        self.initialized = initialized.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.active = True
        self.calls = set()

    def copy(self, *, memo=None):
        # Copy definitions and preserve handlers, never deepcopy a live SDK task.
        return ToolRegistry(tuple(self.get(name) for name in self.names()))

    def derive(self, tools):
        return ToolRegistry(tools)

    def _require_active(self):
        if not self.active:
            raise ValueError("MCP connection scope is closed")

    @asynccontextmanager
    async def operation(self):
        self._require_active()
        task = asyncio.current_task()
        self.calls.add(task)
        try:
            yield
            self._require_active()
        finally:
            self.calls.discard(task)

    async def close(self):
        self.active = False
        tasks = tuple(task for task in self.calls if task is not asyncio.current_task())
        await cancel_tasks(tasks)

    def get_status(self):
        # All server fields are observations; instructions never become prompts.
        return {"state": MCPConnectionState.CONNECTED if self.active else MCPConnectionState.CLOSED,
                "connection_id": self.connection_id,
                "protocol_version": self.initialized.get("protocolVersion"),
                "server_info": deepcopy(self.initialized.get("serverInfo")),
                "capabilities": deepcopy(self.initialized.get("capabilities", {})),
                "instructions": self.initialized.get("instructions"),
                "authentication": "oauth" if self.connector.oauth is not None else "external"}

    async def refresh_tools(self):
        async with self.operation():
            if "tools" not in self.initialized.get("capabilities", {}):
                return ToolRegistry()
            return await self.connector._discover(self.session, client=self)

    def _require_resources(self):
        self._require_active()
        if "resources" not in self.initialized.get("capabilities", {}):
            raise ValueError("MCP server does not advertise resources")

    @staticmethod
    def _page(cursor):
        if cursor is UNSET:
            return {}
        if not isinstance(cursor, str) or not cursor:
            raise ValueError("MCP cursor must be nonempty text")
        from mcp.types import PaginatedRequestParams
        return {"params": PaginatedRequestParams(cursor=cursor)}

    async def list_resources(self, *, cursor=UNSET):
        self._require_resources()
        async with self.operation():
            result = await self.session.list_resources(**self._page(cursor))
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def list_templates(self, *, cursor=UNSET):
        self._require_resources()
        async with self.operation():
            result = await self.session.list_resource_templates(**self._page(cursor))
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def read_resource(self, uri):
        self._require_resources()
        if not isinstance(uri, str) or not uri:
            raise ValueError("MCP resource URI must be nonempty text")
        async with self.operation():
            result = await self.session.read_resource(uri)
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    def _require_prompts(self):
        self._require_active()
        if "prompts" not in self.initialized.get("capabilities", {}):
            raise ValueError("MCP server does not advertise prompts")

    async def list_prompts(self, *, cursor=UNSET):
        self._require_prompts()
        async with self.operation():
            result = await self.session.list_prompts(**self._page(cursor))
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def get_prompt(self, name, *, arguments=UNSET):
        self._require_prompts()
        if not isinstance(name, str) or not name:
            raise ValueError("MCP prompt name must be nonempty text")
        options = {}
        if arguments is not UNSET:
            if arguments is not None and (not isinstance(arguments, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in arguments.items()
            )):
                raise ValueError("MCP prompt arguments must map strings to strings, or be null")
            options["arguments"] = deepcopy(arguments)
        async with self.operation():
            result = await self.session.get_prompt(name, **options)
            # Returned messages are external data, never injected into Engine input.
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)


class MCPElicitationSession:
    """Live request identity; closing a connection cancels all pending replies."""

    def __init__(self, handler):
        self.handler = handler
        self.connection_id = uuid4().hex
        self.active = True
        self.pending = set()

    async def __call__(self, context, params):
        if not self.active:
            raise ValueError("MCP elicitation connection is closed")
        task = asyncio.current_task()
        self.pending.add(task)
        try:
            request = MCPElicitation.request(params, connection_id=self.connection_id, request_id=context.request_id)
            response = await self.handler(request)
            if not self.active:
                raise ValueError("MCP elicitation request is no longer active")
            value = MCPElicitation.response(request, response)
            from mcp.types import ElicitResult
            return ElicitResult.model_validate(value)
        finally:
            self.pending.discard(task)

    async def close(self):
        self.active = False
        tasks = tuple(task for task in self.pending if task is not asyncio.current_task())
        await cancel_tasks(tasks)
