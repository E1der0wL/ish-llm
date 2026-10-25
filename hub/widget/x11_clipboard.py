"""Nonblocking lifetime owner for the isolated native X11 clipboard client."""

from hub.locales.keys import MessageKey
from .clipboard_wire import (
    ClipboardEnvironment,
    ClipboardText,
    ClipboardTransfer,
    X11Wire,
)

import asyncio
from collections.abc import Mapping
from pathlib import Path
import sys


class X11Clipboard:
    """Keep selection ownership across Hub minimization, until replacement/exit.

    Xlib can block or terminate its caller on connection loss. It therefore runs
    in a private Python process, with the shell's DISPLAY/XAUTHORITY unchanged.
    Clipboard contents travel through pipes, never argv, files or terminal output.
    """

    ABSENT = None
    CLOSED = True
    OPEN = False
    RETAINED = True
    NOT_RETAINED = False
    ERRORS = {
        X11Wire.LIBRARY_ERROR: MessageKey.CLIPBOARD_X11_LIBRARY,
        X11Wire.CONNECTION_ERROR: MessageKey.CLIPBOARD_X11_CONNECTION,
        X11Wire.TIMEOUT_ERROR: MessageKey.CLIPBOARD_X11_TIMEOUT,
    }

    def __init__(self) -> None:
        self.owner = self.ABSENT
        self.tasks = set()
        self.closed = self.OPEN
        self.error = self.ABSENT

    def _failed(self, code: int) -> None:
        self.error = self.ERRORS.get(code, MessageKey.CLIPBOARD_X11_TRANSFER)

    async def _reap(self, process: asyncio.subprocess.Process) -> None:
        try:
            await process.wait()
        finally:
            if process.returncode is self.ABSENT:
                process.kill()
                await process.wait()

    async def transfer(
        self, text: str | None, env: Mapping[str, str]
    ) -> str | bool | None:
        self.error = self.ABSENT
        if self.closed or not ClipboardEnvironment(env).display:
            return self.ABSENT
        process = self.ABSENT
        retained = self.NOT_RETAINED
        try:
            async with asyncio.timeout(ClipboardTransfer.TIMEOUT_SECONDS):
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(Path(__file__).with_name(X11Wire.WORKER)),
                    X11Wire.READ if text is self.ABSENT else X11Wire.COPY,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                )
                if text is self.ABSENT:
                    output, _ = await process.communicate()
                    if process.returncode != X11Wire.SUCCESS:
                        self._failed(process.returncode)
                        return self.ABSENT
                    return ClipboardText.decode(output, ClipboardText.UTF8)
                payload = text.encode(ClipboardText.UTF8)
                process.stdin.write(X11Wire.header(len(payload)) + payload)
                await process.stdin.drain()
                if await process.stdout.readline() != X11Wire.OWNED:
                    await process.wait()
                    self._failed(process.returncode)
                    return self.ABSENT
                if self.closed:
                    return self.ABSENT
                previous, self.owner = self.owner, process
                if previous is not self.ABSENT and previous.returncode is self.ABSENT:
                    previous.stdin.close()
                task = asyncio.create_task(self._reap(process))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
                retained = self.RETAINED
                return ClipboardTransfer.SUCCESS
        except TimeoutError:
            self.error = MessageKey.CLIPBOARD_X11_TIMEOUT
            return self.ABSENT
        except (OSError, UnicodeError):
            self.error = MessageKey.CLIPBOARD_X11_TRANSFER
            return self.ABSENT
        finally:
            if process is not self.ABSENT and not retained:
                if process.returncode is self.ABSENT:
                    process.kill()
                await process.wait()

    def close(self) -> None:
        self.closed = self.CLOSED
        if self.owner is not self.ABSENT and self.owner.returncode is self.ABSENT:
            self.owner.stdin.close()
            self.owner.terminate()

    async def aclose(self) -> None:
        self.close()
        if self.tasks:
            await asyncio.gather(*self.tasks)
