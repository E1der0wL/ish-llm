"""Clipboard transport contracts shared with the standalone X11 worker.

Only Python standard-library declarations belong here; importing this module
must not load Hub, prompt_toolkit or Xlib into the isolated worker.
"""

from typing import NamedTuple


class ClipboardText:
    UTF8 = "utf-8"
    UTF16_LE = "utf-16-le"
    ASCII = "ascii"
    CRLF = "\r\n"
    NEWLINE = "\n"
    EMPTY = ""

    @classmethod
    def decode(cls, data, encoding):
        return data.decode(encoding).replace(cls.CRLF, cls.NEWLINE)


class ClipboardEnvironment:
    """Read the caller's open environment without choosing a different desktop."""

    OSC52 = "HUB_OSC52"
    COPY = "HUB_CLIPBOARD_COPY"
    PASTE = "HUB_CLIPBOARD_PASTE"
    TMUX = "TMUX"
    SCREEN = "STY"
    TERM_PROGRAM = "TERM_PROGRAM"
    TERM = "TERM"
    SSH_CONNECTION = "SSH_CONNECTION"
    SSH_TTY = "SSH_TTY"
    WSL_DISTRO = "WSL_DISTRO_NAME"
    WSL_INTEROP = "WSL_INTEROP"
    WAYLAND = "WAYLAND_DISPLAY"
    DISPLAY = "DISPLAY"
    PATH = "PATH"
    ABSENT = None

    def __init__(self, values):
        self.values = values

    @property
    def osc52(self):
        return self.values.get(self.OSC52)

    @property
    def tmux(self):
        return self.values.get(self.TMUX)

    @property
    def multiplexed(self):
        return self.tmux or self.values.get(self.SCREEN)

    @property
    def terminal_program(self):
        return self.values.get(self.TERM_PROGRAM, ClipboardText.EMPTY).lower()

    @property
    def terminal(self):
        return self.values.get(self.TERM, ClipboardText.EMPTY)

    @property
    def remote(self):
        return bool(
            self.values.get(self.SSH_CONNECTION) or self.values.get(self.SSH_TTY)
        )

    @property
    def wsl(self):
        return self.values.get(self.WSL_DISTRO) or self.values.get(self.WSL_INTEROP)

    @property
    def wayland(self):
        return self.values.get(self.WAYLAND)

    @property
    def display(self):
        return self.values.get(self.DISPLAY)

    @property
    def copy_override(self):
        return self.values.get(self.COPY)

    def bridge(self, read):
        return self.values.get(
            self.PASTE if read else self.COPY, ClipboardText.EMPTY
        ).strip()

    def search_path(self, default):
        return self.values.get(self.PATH, default)


class ClipboardCommand(NamedTuple):
    executable: str
    arguments: list[str]
    encoding: str


class ClipboardCommands:
    DEFAULT_READ = False
    ABSENT = None
    POWERSHELL = "powershell.exe"
    POWERSHELL_ARGUMENTS = (
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
        "[Console]::Write((Get-Clipboard -Raw))",
    )
    CLIP = "clip.exe"
    WL_PASTE = "wl-paste"
    WL_PASTE_ARGUMENTS = ("--no-newline",)
    WL_COPY = "wl-copy"
    XCLIP = "xclip"
    XCLIP_SELECTION = ("-selection", "clipboard")
    XCLIP_READ = "-o"
    XCLIP_WRITE = "-i"
    XSEL = "xsel"
    XSEL_SELECTION = "--clipboard"
    XSEL_READ = "--output"
    XSEL_WRITE = "--input"


class TerminalClipboard:
    ENABLED = True
    DISABLED = False
    OVERRIDE_ENABLED = "1"
    ISATTY = "isatty"
    PROGRAMS = {"wezterm", "iterm.app", "kitty", "foot"}
    TERMINALS = {"xterm-kitty", "foot", "foot-extra"}
    MAX_BASE64_LENGTH = 7000
    OSC_PREFIX = "\x1b]52;c;"
    OSC_END = "\x07"
    TMUX_PREFIX = "\x1bPtmux;"
    ESCAPE = "\x1b"
    ESCAPED_ESCAPE = "\x1b\x1b"
    TMUX_END = "\x1b\\"


class ClipboardTransfer:
    ABSENT = None
    SUCCESS = True
    EXIT_SUCCESS = 0
    TIMEOUT_SECONDS = 3


class X11Wire:
    WORKER = "_x11_selection.py"
    READ = "read"
    COPY = "copy"
    HEADER_BYTES = 8
    BYTE_ORDER = "big"
    OWNED = b"owned\n"
    SUCCESS = 0
    TRANSFER_ERROR = 1
    LIBRARY_ERROR = 2
    CONNECTION_ERROR = 3
    TIMEOUT_ERROR = 4

    @classmethod
    def header(cls, length):
        return length.to_bytes(cls.HEADER_BYTES, cls.BYTE_ORDER)

    @classmethod
    def length(cls, header):
        return int.from_bytes(header, cls.BYTE_ORDER)
