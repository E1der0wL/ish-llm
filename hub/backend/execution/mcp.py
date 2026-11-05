"""Hub's opt-in-at-use MCP transport; backend owns protocol and Tool execution."""

import asyncio
import os
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
    ENCODING = 'utf-8'
    DECODING_ERRORS = 'replace'
    STREAM_MODE = 'w+b'
    STDERR_EVENT = 'mcp_server_stderr'

    @classmethod
    def _stderr_tail(cls, stream):
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - cls.STDERR_TAIL_BYTES))
        return stream.read().decode(cls.ENCODING, errors=cls.DECODING_ERRORS).strip()

    @asynccontextmanager
    async def __call__(self, definition):
        with TemporaryFile(mode=self.STREAM_MODE) as stderr:
            try:
                async with MCPConnector(errlog=stderr)(definition) as client:
                    yield client
            finally:
                text = await asyncio.to_thread(self._stderr_tail, stderr)
                if text:
                    diagnostic(self.STDERR_EVENT, stderr_tail=text)
