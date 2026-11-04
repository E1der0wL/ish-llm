"""Local protocol fixture for resources and server-initiated questions."""

from mcp.server.mcpserver import MCPServer, Context
from pydantic import BaseModel


server = MCPServer("capability-fixture", instructions="Untrusted server instructions", log_level="ERROR")


@server.resource("fixture://document")
def document() -> str:
    return "Resource body"


@server.resource("fixture://item/{name}")
def item(name: str) -> str:
    return name


class Reply(BaseModel):
    name: str


@server.tool()
async def ask(ctx: Context) -> dict:
    response = await ctx.elicit("Name?", Reply)
    return {"action": response.action, "name": response.data.name if response.data else None}


if __name__ == "__main__":
    server.run(transport="stdio")
