"""로컬 MCP transport 통합 검사 전용 서버. 배포 플러그인에 포함하지 않는다."""

import asyncio
import os
from pathlib import Path
import sys
from mcp.server.fastmcp import FastMCP


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    server = FastMCP("test-server", **({"port": port} if port else {}), log_level="ERROR")
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
    async def wait() -> str:
        """Wait until cancelled."""
        await asyncio.Event().wait()
        return "unreachable"

    server.run(transport="streamable-http" if port else "stdio")
