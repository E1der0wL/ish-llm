"""Hub's opt-in-at-use MCP transport; backend owns protocol and Tool execution."""

import asyncio
from contextlib import asynccontextmanager
from tempfile import TemporaryFile

from llm.providers.mcp import MCPConnector
from llm.providers.runtime import diagnostic


class HubMCPConnector:
    """Keep server stderr off the TUI and use existing provider diagnostics.

    No connection is opened at construction or definition CRUD. Each context is
    opened, used and closed by the caller's task. Timeout is deliberately omitted:
    SDK defaults are not replaced by an invented unlimited timeout.
    """

    STDERR_TAIL_BYTES = 8192

    @classmethod
    def _stderr_tail(cls, stream):
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - cls.STDERR_TAIL_BYTES))
        return stream.read().decode("utf-8", errors="replace").strip()

    @asynccontextmanager
    async def __call__(self, definition):
        with TemporaryFile(mode="w+b") as stderr:
            try:
                async with MCPConnector(errlog=stderr)(definition) as client:
                    yield client
            finally:
                text = await asyncio.to_thread(self._stderr_tail, stderr)
                if text:
                    diagnostic("mcp_server_stderr", stderr_tail=text)
