"""로컬 MCP transport 통합 검사 전용 서버. 배포 플러그인에 포함하지 않는다."""

import asyncio
import os
from pathlib import Path
import sys
from mcp.server.mcpserver import MCPServer, Context
from mcp.types import ListToolsResult


class PagedServer(MCPServer):
    async def _handle_list_tools(self, ctx, params):
        result = await super()._handle_list_tools(ctx, params)
        index = int(params.cursor) if params is not None and params.cursor is not None else 0
        return ListToolsResult(tools=result.tools[index:index + 1],
                               next_cursor=str(index + 1) if index + 1 < len(result.tools) else None)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    server = PagedServer("test-server", log_level="ERROR")
    marker = os.environ.get("MCP_TEST_MARKER")
    if marker:
        with Path(marker).open("a") as output:
            output.write("start\n")

    @server.tool()
    def echo(value: str) -> dict:
        """Echo explicitly supplied text."""
        if marker:
            with Path(marker).open("a") as output:
                output.write("effect\n")
        return {"value": value}

    @server.tool()
    def fail() -> str:
        """Report a remote Tool error."""
        raise ValueError("remote failure")

    @server.tool()
    def crash() -> str:
        """Persist a fixture effect, then die before returning any receipt."""
        if marker is None:
            raise ValueError("Crash fixture requires a marker path")
        with Path(marker).open("a") as output:
            output.write("crash-effect\n")
            output.flush()
            os.fsync(output.fileno())
        os._exit(73)

    @server.tool()
    def authorization(ctx: Context) -> dict:
        """Return the fixture's received HTTP authentication header."""
        return {"authorization": (ctx.headers or {}).get("Authorization")}

    @server.tool()
    async def wait() -> str:
        """Wait until cancelled."""
        await asyncio.Event().wait()
        return "unreachable"

    server.run(transport="streamable-http" if port else "stdio", **({"port": port} if port else {}))
