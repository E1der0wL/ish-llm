"""Render Rich progress segments into the host-owned popup without terminal I/O."""
from io import StringIO

from rich.console import Console
from rich.progress_bar import ProgressBar
from rich.style import Style

from ..core.theme import Theme, ThemeRole
from .presentation import TextRow, TextSpan


def progress_bar(completed: int, total: int, width: int, theme: Theme) -> TextRow:
    if width <= 0:
        return TextRow()
    styles = {role: Style(color=theme.resolve(role) or "default")
              for role in (ThemeRole.MUTED, ThemeRole.TITLE, ThemeRole.SAVE)}
    bar = ProgressBar(total=total, completed=completed, width=width,
                      style=styles[ThemeRole.MUTED], complete_style=styles[ThemeRole.TITLE],
                      finished_style=styles[ThemeRole.SAVE])
    console = Console(file=StringIO(), width=width, force_terminal=True,
                      color_system="truecolor", no_color=False, legacy_windows=False)
    # Rich owns bar glyphs and partial cells. Preserve role references so a null
    # theme role still follows the terminal instead of acquiring an RGB fallback.
    return TextRow(tuple(TextSpan(segment.text, next(role for role, style in styles.items()
                                                    if style == segment.style))
                         for segment in console.render(bar) if not segment.control))
