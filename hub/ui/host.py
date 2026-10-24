"""Host editor and desktop integration shared by settings and component managers."""

import asyncio
from pathlib import Path
import subprocess
import tempfile

from prompt_toolkit.application import in_terminal
from prompt_toolkit.application.current import get_app

from ..config.general import GeneralSettings


class HostEditor:
    TEXT_SUFFIX = ".txt"
    TEMPORARY_PREFIX = "ish-hub-edit-"
    VALUE_NAME = "value"
    ENCODING = "utf-8"
    NEWLINE = "\n"
    TERMINATE_WAIT_SECONDS = 2
    RUNNING = None


class DesktopOpener:
    EXECUTABLE = "xdg-open"


async def edit_text(
    text: str, settings: GeneralSettings, *, suffix: str = HostEditor.TEXT_SUFFIX
) -> str:
    app = get_app()
    with tempfile.TemporaryDirectory(prefix=HostEditor.TEMPORARY_PREFIX) as directory:
        path = Path(directory) / (HostEditor.VALUE_NAME + suffix)
        path.write_text(text, encoding=HostEditor.ENCODING)
        argv = settings.editor_argv(path)
        async with in_terminal():
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=app.input.fileno(),
                stdout=app.output.fileno(),
                stderr=app.output.fileno()
            )
            try:
                code = await process.wait()
                if code:
                    raise subprocess.CalledProcessError(code, argv)
            finally:
                if process.returncode is HostEditor.RUNNING:
                    process.terminate()
                    try:
                        await asyncio.wait_for(
                            process.wait(), timeout=HostEditor.TERMINATE_WAIT_SECONDS
                        )
                    except TimeoutError:
                        process.kill()
                        await process.wait()
        edited = path.read_text(encoding=HostEditor.ENCODING)
        # Editors conventionally add a final newline even to scalar settings.
        return (
            edited
            if text.endswith(HostEditor.NEWLINE)
            else edited.removesuffix(HostEditor.NEWLINE)
        )


async def open_directory(path: str) -> int:
    process = await asyncio.create_subprocess_exec(
        DesktopOpener.EXECUTABLE,
        path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await process.wait()
