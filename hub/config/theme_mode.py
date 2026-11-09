"""Display mode selection, without acquiring the host's terminal input."""

from dataclasses import replace
from enum import StrEnum
import os


class ThemeMode(StrEnum):
    AUTO = "auto"
    DARK = "dark"
    LIGHT = "light"


class TerminalTheme:
    """Use a terminal-provided hint once per configuration application.

    COLORFGBG uses terminal palette indices. White/light-gray backgrounds are
    light; absent or unrecognized hints retain dark. A remote host's desktop
    preference cannot establish the SSH client's terminal background. Do not
    send OSC queries without a host-owned reply channel or read stdin here.
    """

    BACKGROUND_HINT = "COLORFGBG"
    SEPARATOR = ";"
    LIGHT_BACKGROUNDS = frozenset(("7", "15"))

    @classmethod
    def resolve(cls, theme, environment=None):
        if theme.mode != ThemeMode.AUTO:
            return theme
        environment = os.environ if environment is None else environment
        background = environment.get(cls.BACKGROUND_HINT, "").split(cls.SEPARATOR)[-1]
        mode = ThemeMode.LIGHT if background in cls.LIGHT_BACKGROUNDS else ThemeMode.DARK
        # Only this transient render copy is resolved; persisted settings keep
        # AUTO and the original palette. No I/O or detection runs while painting.
        return replace(theme, mode=mode)
