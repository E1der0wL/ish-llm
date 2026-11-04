"""Public optional MCP client operations; SDK values remain adapter-owned."""

from typing import Protocol
from llm.core.configuration import UNSET


class MCPClient(Protocol):
    """An extended connector result, also implementing the ToolRegistry contract.

    Plain ToolRegistry connectors remain valid for Tools. Optional operations
    fail explicitly when the selected connector does not implement them.
    Returned dictionaries are detached external protocol observations, not policy.
    """

    def get_status(self) -> dict: ...
    async def refresh_tools(self): ...
    async def list_resources(self, *, cursor=UNSET) -> dict: ...
    async def list_templates(self, *, cursor=UNSET) -> dict: ...
    async def read_resource(self, uri: str) -> dict: ...


class MCPClientOperation:
    """Names of optional public operations, never provider-private options."""

    STATUS = MCPClient.get_status.__name__
    REFRESH = MCPClient.refresh_tools.__name__
    RESOURCES = MCPClient.list_resources.__name__
    TEMPLATES = MCPClient.list_templates.__name__
    READ = MCPClient.read_resource.__name__

    @staticmethod
    def require(client, name):
        operation = getattr(client, name, None)
        if not callable(operation):
            raise ValueError(f"Selected MCP connector does not support {name}")
        return operation
