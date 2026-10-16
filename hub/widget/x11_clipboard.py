"""Nonblocking lifetime owner for the isolated native X11 clipboard client."""

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

    def __init__(self) -> None:
        self.owner = None
        self.tasks = set()
        self.closed = False
        self.error = None

    def _failed(self, code: int) -> None:
        self.error = {2: "clipboard_x11_library", 3: "clipboard_x11_connection", 4: "clipboard_x11_timeout"}.get(
            code, "clipboard_x11_transfer")

    async def _reap(self, process: asyncio.subprocess.Process) -> None:
        try:
            await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def transfer(self, text: str | None, env: Mapping[str, str]) -> str | bool | None:
        self.error = None
        if self.closed or not env.get("DISPLAY"):
            return None
        process = None
        retained = False
        try:
            async with asyncio.timeout(3):
                process = await asyncio.create_subprocess_exec(
                    sys.executable, str(Path(__file__).with_name("_x11_selection.py")),
                    "read" if text is None else "copy",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, env=env)
                if text is None:
                    output, _ = await process.communicate()
                    if process.returncode != 0:
                        self._failed(process.returncode)
                        return None
                    return output.decode("utf-8").replace("\r\n", "\n")
                payload = text.encode("utf-8")
                process.stdin.write(len(payload).to_bytes(8, "big") + payload)
                await process.stdin.drain()
                if await process.stdout.readline() != b"owned\n":
                    await process.wait()
                    self._failed(process.returncode)
                    return None
                if self.closed:
                    return None
                previous, self.owner = self.owner, process
                if previous is not None and previous.returncode is None:
                    previous.stdin.close()
                task = asyncio.create_task(self._reap(process))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
                retained = True
                return True
        except TimeoutError:
            self.error = "clipboard_x11_timeout"
            return None
        except (OSError, UnicodeError):
            self.error = "clipboard_x11_transfer"
            return None
        finally:
            if process is not None and not retained:
                if process.returncode is None:
                    process.kill()
                await process.wait()

    def close(self) -> None:
        self.closed = True
        if self.owner is not None and self.owner.returncode is None:
            self.owner.stdin.close()
            self.owner.terminate()

    async def aclose(self) -> None:
        self.close()
        if self.tasks:
            await asyncio.gather(*self.tasks)
