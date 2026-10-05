"""Edit a settings draft while lending the host terminal to the configured editor."""

import asyncio
from pathlib import Path
import subprocess
import tempfile

from prompt_toolkit.application import in_terminal
from prompt_toolkit.application.current import get_app

from ...config.general import GeneralSettings


async def edit_text(text: str, settings: GeneralSettings, *, suffix: str = ".txt") -> str:
    app = get_app()
    with tempfile.TemporaryDirectory(prefix="ish-hub-edit-") as directory:
        path = Path(directory) / ("value" + suffix)
        path.write_text(text, encoding="utf-8")
        argv = settings.editor_argv(path)
        async with in_terminal():
            process = await asyncio.create_subprocess_exec(
                *argv, stdin=app.input.fileno(), stdout=app.output.fileno(), stderr=app.output.fileno())
            try:
                code = await process.wait()
                if code:
                    raise subprocess.CalledProcessError(code, argv)
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
        edited = path.read_text(encoding="utf-8")
        # Editors conventionally add a final newline even to scalar settings.
        return edited if text.endswith("\n") else edited.removesuffix("\n")
