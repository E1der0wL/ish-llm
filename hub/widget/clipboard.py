"""Hub's clipboard register, backed by the desktop or terminal clipboard."""

from hub.locales.keys import MessageKey
from .output.selection import SelectableControl
from .clipboard_wire import (
    ClipboardText,
    ClipboardEnvironment,
    ClipboardCommand,
    ClipboardCommands,
    TerminalClipboard,
    ClipboardTransfer,
)

import asyncio
import base64
import os
import shlex
import shutil

from prompt_toolkit.application.current import get_app
from prompt_toolkit.clipboard import Clipboard, ClipboardData, InMemoryClipboard
from prompt_toolkit.output.vt100 import Vt100_Output


def terminal_clipboard_supported(app, env=ClipboardTransfer.ABSENT):
    """Only use OSC 52 on a real terminal with an identified write capability.

    Generic xterm/VM consoles and shell output proxies can print unsupported OSC
    payloads literally. HUB_OSC52=1 is an explicit override for a verified host.
    """
    if not isinstance(app.output, Vt100_Output):
        return TerminalClipboard.DISABLED
    stream = app.output.stdout
    if not getattr(
        stream, TerminalClipboard.ISATTY, lambda: TerminalClipboard.DISABLED
    )():
        return TerminalClipboard.DISABLED
    env = ClipboardEnvironment(os.environ if env is ClipboardTransfer.ABSENT else env)
    override = env.osc52
    if override is not ClipboardTransfer.ABSENT:
        return override == TerminalClipboard.OVERRIDE_ENABLED
    # Multiplexers need their own verified passthrough configuration.
    if env.multiplexed:
        return TerminalClipboard.DISABLED
    return (
        env.terminal_program in TerminalClipboard.PROGRAMS
        or env.terminal in TerminalClipboard.TERMINALS
    )


def commands(read=ClipboardCommands.DEFAULT_READ, env=ClipboardCommands.ABSENT):
    env = ClipboardEnvironment(os.environ if env is ClipboardCommands.ABSENT else env)
    custom = env.bridge(read)
    if custom:
        argv = shlex.split(custom)
        if argv and argv[0]:
            yield ClipboardCommand(argv[0], argv[1:], ClipboardText.UTF8)
        # An explicit bridge is authoritative, including failure.
        return
    remote = env.remote
    # SSH forwarding must not fall back to the remote machine's desktop.
    if not remote and env.wsl:
        if read:
            yield ClipboardCommand(
                ClipboardCommands.POWERSHELL,
                list(ClipboardCommands.POWERSHELL_ARGUMENTS),
                ClipboardText.UTF8,
            )
        else:
            yield ClipboardCommand(ClipboardCommands.CLIP, [], ClipboardText.UTF16_LE)
    if not remote and env.wayland:
        yield (
            ClipboardCommand(
                ClipboardCommands.WL_PASTE,
                list(ClipboardCommands.WL_PASTE_ARGUMENTS),
                ClipboardText.UTF8,
            )
            if read
            else ClipboardCommand(ClipboardCommands.WL_COPY, [], ClipboardText.UTF8)
        )
    if env.display:
        yield ClipboardCommand(
            ClipboardCommands.XCLIP,
            [
                *ClipboardCommands.XCLIP_SELECTION,
                ClipboardCommands.XCLIP_READ if read else ClipboardCommands.XCLIP_WRITE,
            ],
            ClipboardText.UTF8,
        )
        yield ClipboardCommand(
            ClipboardCommands.XSEL,
            [
                ClipboardCommands.XSEL_SELECTION,
                ClipboardCommands.XSEL_READ if read else ClipboardCommands.XSEL_WRITE,
            ],
            ClipboardText.UTF8,
        )


async def system_clipboard(
    text=ClipboardTransfer.ABSENT,
    *,
    env=ClipboardTransfer.ABSENT,
    x11=ClipboardTransfer.ABSENT
):
    read = text is ClipboardTransfer.ABSENT
    if x11 is not ClipboardTransfer.ABSENT:
        x11.error = ClipboardTransfer.ABSENT
    env = dict(os.environ if env is ClipboardTransfer.ABSENT else env)
    try:
        candidates = list(commands(read, env))
    except ValueError:
        return ClipboardTransfer.ABSENT
    for name, args, encoding in candidates:
        executable = shutil.which(
            name, path=ClipboardEnvironment(env).search_path(os.defpath)
        )
        if not executable:
            continue
        process = ClipboardTransfer.ABSENT
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *args,
                stdin=asyncio.subprocess.DEVNULL if read else asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE if read else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env
            )
            # A cold PowerShell/desktop clipboard service can take over one
            # second. This wait is asynchronous and never blocks rendering.
            async with asyncio.timeout(ClipboardTransfer.TIMEOUT_SECONDS):
                stdout, _ = await process.communicate(
                    ClipboardTransfer.ABSENT if read else text.encode(encoding)
                )
            if process.returncode == ClipboardTransfer.EXIT_SUCCESS:
                return (
                    ClipboardText.decode(stdout, encoding)
                    if read
                    else ClipboardTransfer.SUCCESS
                )
        except (OSError, UnicodeError, TimeoutError):
            pass
        finally:
            if (
                process is not ClipboardTransfer.ABSENT
                and process.returncode is ClipboardTransfer.ABSENT
            ):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
    if (
        x11 is not ClipboardTransfer.ABSENT
        and ClipboardEnvironment(env).display
        and not ClipboardEnvironment(env).bridge(read)
    ):
        return await x11.transfer(text, env)
    return ClipboardTransfer.ABSENT


async def export_text(
    app, text, *, env=ClipboardTransfer.ABSENT, x11=ClipboardTransfer.ABSENT
):
    env = dict(os.environ if env is ClipboardTransfer.ABSENT else env)
    if await system_clipboard(text, env=env, x11=x11):
        return MessageKey.CLIPBOARD_COPIED
    payload = base64.b64encode(text.encode(ClipboardText.UTF8)).decode(
        ClipboardText.ASCII
    )
    # Bound the entire sequence: some terminals print the remainder of an
    # oversized control string as text. Always retain the full internal register.
    if (
        ClipboardEnvironment(env).copy_override
        or not terminal_clipboard_supported(app, env)
        or len(payload) > TerminalClipboard.MAX_BASE64_LENGTH
    ):
        return MessageKey.CLIPBOARD_INTERNAL
    sequence = TerminalClipboard.OSC_PREFIX + payload + TerminalClipboard.OSC_END
    if ClipboardEnvironment(env).tmux:
        sequence = (
            TerminalClipboard.TMUX_PREFIX
            + sequence.replace(
                TerminalClipboard.ESCAPE, TerminalClipboard.ESCAPED_ESCAPE
            )
            + TerminalClipboard.TMUX_END
        )
    app.output.write_raw(sequence)
    app.output.flush()
    return MessageKey.CLIPBOARD_TERMINAL


class SystemClipboard(Clipboard):
    """Synchronous PTK register with nonblocking system writes and paste reads."""

    ABSENT = None
    INITIAL_REVISION = 0

    def __init__(self, app, original, *, environment=ABSENT, x11=ABSENT):
        from .x11_clipboard import X11Clipboard

        self.app, self.original = app, original
        self.x11 = x11 if x11 is not self.ABSENT else X11Clipboard()
        self.environment = environment or (lambda: dict(os.environ))
        self.cache = InMemoryClipboard()
        self.cache.set_data(original.get_data())
        self.pending = self.ABSENT
        self.revision = self.INITIAL_REVISION

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
                self._write(data, dict(self.environment()), self.pending, self.revision)
            )

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
            return self.ABSENT
        if text is self.ABSENT:
            return self.ABSENT
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
    if view.mouse.app is SelectableControl.ABSENT:
        return ClipboardText.EMPTY
    scope = view.mouse._scope()
    for window, _ in reversed(view.mouse.windows):
        selection = SelectableControl.selection_of(window.content)
        if (
            window in scope
            and selection is not SelectableControl.ABSENT
            and selection.text
        ):
            return selection.text
    return ClipboardText.EMPTY
