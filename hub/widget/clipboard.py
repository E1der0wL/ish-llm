"""Hub's clipboard register, backed by the desktop or terminal clipboard."""

import asyncio
import base64
import os
import shutil

from prompt_toolkit.application.current import get_app
from prompt_toolkit.clipboard import Clipboard, ClipboardData, InMemoryClipboard
from prompt_toolkit.output.vt100 import Vt100_Output


def terminal_clipboard_supported(app):
    """Only use OSC 52 on a real terminal with an identified write capability.

    Generic xterm/VM consoles and shell output proxies can print unsupported OSC
    payloads literally. HUB_OSC52=1 is an explicit override for a verified host.
    """
    if not isinstance(app.output, Vt100_Output):
        return False
    stream = app.output.stdout
    if not getattr(stream, "isatty", lambda: False)():
        return False
    override = os.environ.get("HUB_OSC52")
    if override is not None:
        return override == "1"
    # Multiplexers need their own verified passthrough configuration.
    if os.environ.get("TMUX") or os.environ.get("STY"):
        return False
    return (os.environ.get("TERM_PROGRAM", "").lower() in {"wezterm", "iterm.app", "kitty", "foot"}
            or os.environ.get("TERM", "") in {"xterm-kitty", "foot", "foot-extra"})


def commands(read=False):
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        if read:
            yield "powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
                "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
                "[Console]::Write((Get-Clipboard -Raw))"], "utf-8"
        else:
            yield "clip.exe", [], "utf-16-le"
    if os.environ.get("WAYLAND_DISPLAY"):
        yield ("wl-paste", ["--no-newline"], "utf-8") if read else ("wl-copy", [], "utf-8")
    if os.environ.get("DISPLAY"):
        yield "xclip", ["-selection", "clipboard", "-o" if read else "-i"], "utf-8"
        yield "xsel", ["--clipboard", "--output" if read else "--input"], "utf-8"


async def system_clipboard(text=None):
    read = text is None
    for name, args, encoding in commands(read):
        executable = shutil.which(name)
        if not executable:
            continue
        process = None
        try:
            process = await asyncio.create_subprocess_exec(executable, *args,
                stdin=asyncio.subprocess.DEVNULL if read else asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE if read else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            async with asyncio.timeout(1):
                stdout, _ = await process.communicate(None if read else text.encode(encoding))
            if process.returncode == 0:
                return stdout.decode(encoding).replace("\r\n", "\n") if read else True
        except (OSError, UnicodeError, TimeoutError):
            pass
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
    return None


async def export_text(app, text):
    if await system_clipboard(text):
        return "clipboard_copied"
    payload = base64.b64encode(text.encode()).decode("ascii")
    # Bound the entire sequence: some terminals print the remainder of an
    # oversized control string as text. Always retain the full internal register.
    if not terminal_clipboard_supported(app) or len(payload) > 7000:
        return "clipboard_internal"
    sequence = "\x1b]52;c;" + payload + "\x07"
    if os.environ.get("TMUX"):
        sequence = "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    app.output.write_raw(sequence)
    app.output.flush()
    return "clipboard_terminal"


class SystemClipboard(Clipboard):
    """Synchronous PTK register with nonblocking system writes and paste reads."""

    def __init__(self, app, original):
        self.app, self.original = app, original
        self.cache = InMemoryClipboard()
        self.cache.set_data(original.get_data())
        self.pending = None

    def set_data(self, data):
        self.cache.set_data(data)
        if self.app.is_running:
            previous = self.pending
            async def write():
                if previous:
                    await previous
                try:
                    await export_text(self.app, data.text)
                except OSError:
                    pass
            self.pending = self.app.create_background_task(write())

    def get_data(self):
        return self.cache.get_data()

    def rotate(self):
        self.cache.rotate()

    async def read(self):
        if self.pending:
            await self.pending
        text = await system_clipboard()
        if text is not None:
            self.cache.set_data(ClipboardData(text))
        return self.get_data()


async def copy_text(text):
    app = get_app()
    register = app.clipboard
    if isinstance(register, SystemClipboard):
        if register.pending:
            await register.pending
        register.cache.set_data(ClipboardData(text))
    else:
        register.set_data(ClipboardData(text))
    return await export_text(app, text)


def selected_text(app, view):
    """Only copy selections visible in the current modal/page scope."""
    buffer = app.current_buffer
    if buffer.selection_state:
        start, end = buffer.document.selection_range()
        return buffer.text[start:end]
    if view.mouse.app is None:
        return ""
    scope = view.mouse._scope()
    for window, _ in reversed(view.mouse.windows):
        selection = getattr(window.content, "selection", None)
        if window in scope and selection is not None and selection.text:
            return selection.text
    return ""
