"""Copy explicitly selected text to PTK and the host terminal clipboard."""

import asyncio
import base64
import os
import shutil

from prompt_toolkit.application.current import get_app
from prompt_toolkit.clipboard import ClipboardData


async def copy_text(text):
    app = get_app()
    app.clipboard.set_data(ClipboardData(text))
    candidates = (["wl-copy"] if os.environ.get("WAYLAND_DISPLAY") else [])
    candidates += ["xclip", "xsel"] if os.environ.get("DISPLAY") else []
    for name in candidates:
        executable = shutil.which(name)
        if executable is None:
            continue
        args = {"wl-copy": [], "xclip": ["-selection", "clipboard"], "xsel": ["--clipboard", "--input"]}[name]
        process = await asyncio.create_subprocess_exec(executable, *args, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(3):
                await process.communicate(text.encode())
            if process.returncode == 0:
                return "clipboard_copied"
        except TimeoutError:
            process.kill()
            await process.wait()
    # OSC 52 works with SSH terminals that allow clipboard writes. PTK still
    # retains the full text when the terminal disables this capability.
    app.output.write_raw("\x1b]52;c;" + base64.b64encode(text.encode()).decode("ascii") + "\x07")
    app.output.flush()
    return "clipboard_terminal"
