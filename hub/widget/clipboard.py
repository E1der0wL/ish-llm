"""Hub's clipboard register, backed by the desktop or terminal clipboard."""

from hub.locales.keys import MessageKey

import asyncio
import base64
import os
import shlex
import shutil

from prompt_toolkit.application.current import get_app
from prompt_toolkit.clipboard import Clipboard, ClipboardData, InMemoryClipboard
from prompt_toolkit.output.vt100 import Vt100_Output


def terminal_clipboard_supported(app, env=None):
    """Only use OSC 52 on a real terminal with an identified write capability.

    Generic xterm/VM consoles and shell output proxies can print unsupported OSC
    payloads literally. HUB_OSC52=1 is an explicit override for a verified host.
    """
    if not isinstance(app.output, Vt100_Output):
        return False
    stream = app.output.stdout
    if not getattr(stream, "isatty", lambda: False)():
        return False
    env = os.environ if env is None else env
    override = env.get("HUB_OSC52")
    if override is not None:
        return override == "1"
    # Multiplexers need their own verified passthrough configuration.
    if env.get("TMUX") or env.get("STY"):
        return False
    return (env.get("TERM_PROGRAM", "").lower() in {"wezterm", "iterm.app", "kitty", "foot"}
            or env.get("TERM", "") in {"xterm-kitty", "foot", "foot-extra"})


def commands(read=False, env=None):
    env = os.environ if env is None else env
    custom = env.get("HUB_CLIPBOARD_PASTE" if read else "HUB_CLIPBOARD_COPY", "").strip()
    if custom:
        argv = shlex.split(custom)
        if argv and argv[0]:
            yield argv[0], argv[1:], "utf-8"
        # An explicit bridge is authoritative; do not silently use a different
        # display or the remote machine's clipboard if that bridge fails.
        return
    remote = bool(env.get("SSH_CONNECTION") or env.get("SSH_TTY"))
    # SSH's forwarded DISPLAY belongs to the connecting desktop. A Wayland or
    # WSL clipboard on the remote host may instead belong to a different user
    # session, so never silently fall back to it from an SSH session.
    if not remote and (env.get("WSL_DISTRO_NAME") or env.get("WSL_INTEROP")):
        if read:
            yield "powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
                "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
                "[Console]::Write((Get-Clipboard -Raw))"], "utf-8"
        else:
            yield "clip.exe", [], "utf-16-le"
    if not remote and env.get("WAYLAND_DISPLAY"):
        yield ("wl-paste", ["--no-newline"], "utf-8") if read else ("wl-copy", [], "utf-8")
    if env.get("DISPLAY"):
        yield "xclip", ["-selection", "clipboard", "-o" if read else "-i"], "utf-8"
        yield "xsel", ["--clipboard", "--output" if read else "--input"], "utf-8"


async def system_clipboard(text=None, *, env=None, x11=None):
    read = text is None
    if x11 is not None:
        x11.error = None
    env = dict(os.environ if env is None else env)
    try:
        candidates = list(commands(read, env))
    except ValueError:
        return None
    for name, args, encoding in candidates:
        executable = shutil.which(name, path=env.get("PATH", os.defpath))
        if not executable:
            continue
        process = None
        try:
            process = await asyncio.create_subprocess_exec(executable, *args,
                stdin=asyncio.subprocess.DEVNULL if read else asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE if read else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, env=env)
            # A cold PowerShell/desktop clipboard service can take over one
            # second. This wait is asynchronous and never blocks rendering.
            async with asyncio.timeout(3):
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
    override = "HUB_CLIPBOARD_PASTE" if read else "HUB_CLIPBOARD_COPY"
    if x11 is not None and env.get("DISPLAY") and not env.get(override, "").strip():
        return await x11.transfer(text, env)
    return None


async def export_text(app, text, *, env=None, x11=None):
    env = dict(os.environ if env is None else env)
    if await system_clipboard(text, env=env, x11=x11):
        return MessageKey.CLIPBOARD_COPIED
    payload = base64.b64encode(text.encode()).decode("ascii")
    # Bound the entire sequence: some terminals print the remainder of an
    # oversized control string as text. Always retain the full internal register.
    if env.get("HUB_CLIPBOARD_COPY") or not terminal_clipboard_supported(app, env) or len(payload) > 7000:
        return MessageKey.CLIPBOARD_INTERNAL
    sequence = "\x1b]52;c;" + payload + "\x07"
    if env.get("TMUX"):
        sequence = "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    app.output.write_raw(sequence)
    app.output.flush()
    return MessageKey.CLIPBOARD_TERMINAL


class SystemClipboard(Clipboard):
    """Synchronous PTK register with nonblocking system writes and paste reads."""

    def __init__(self, app, original, *, environment=None, x11=None):
        from .x11_clipboard import X11Clipboard
        self.app, self.original = app, original
        self.x11 = x11 if x11 is not None else X11Clipboard()
        self.environment = environment or (lambda: dict(os.environ))
        self.cache = InMemoryClipboard()
        self.cache.set_data(original.get_data())
        self.pending = None
        self.revision = 0

    def remember(self, data):
        """Accept terminal paste without writing it back to the desktop."""
        self.revision += 1
        self.cache.set_data(data)

    async def _write(self, data, env, previous, revision):
        if previous:
            await asyncio.shield(previous)
        # A terminal paste or a newer copy supersedes a write still in queue.
        if revision != self.revision:
            return MessageKey.CLIPBOARD_INTERNAL
        try:
            result = await export_text(self.app, data.text, env=env, x11=self.x11)
        except OSError:
            result = MessageKey.CLIPBOARD_INTERNAL
        return result

    def set_data(self, data):
        self.remember(data)
        if self.app.is_running:
            self.pending = self.app.create_background_task(
                self._write(data, dict(self.environment()), self.pending, self.revision))

    def get_data(self):
        return self.cache.get_data()

    def rotate(self):
        self.cache.rotate()

    async def read(self):
        revision = self.revision
        if self.pending:
            await asyncio.shield(self.pending)
        text = await system_clipboard(env=dict(self.environment()), x11=self.x11)
        # Do not overwrite a newer terminal paste/copy with a slow read result.
        if revision != self.revision:
            return None
        if text is None:
            return None
        self.remember(ClipboardData(text))
        return self.get_data()


async def copy_text(text):
    app = get_app()
    register = app.clipboard
    if isinstance(register, SystemClipboard):
        register.set_data(ClipboardData(text))
        if register.pending:
            return await asyncio.shield(register.pending)
        return MessageKey.CLIPBOARD_INTERNAL
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
