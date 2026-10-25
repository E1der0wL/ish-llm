"""Shared Rich-to-terminal rendering options at the Hub display boundary."""

from rich.console import Console


class RichTerminal:
    COLOR_SYSTEM = "truecolor"
    FORCE_TERMINAL = True
    HIGHLIGHT = False

    @classmethod
    def console(cls, *, file, width, **kwargs):
        return Console(
            file=file,
            width=width,
            color_system=cls.COLOR_SYSTEM,
            force_terminal=cls.FORCE_TERMINAL,
            highlight=cls.HIGHLIGHT,
            **kwargs
        )
