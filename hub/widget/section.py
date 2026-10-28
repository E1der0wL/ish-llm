"""Shared section heading and rule."""

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.widgets import Label

from ..asset.icon import RULE
from ..config.theme_style import ThemeSelector, ThemeStyle

TITLE_STYLE = ThemeStyle.joined(ThemeSelector.ACCENT.style, ThemeStyle.BOLD)
RULE_HEIGHT = 1


class Heading(Label):
    """A category or field caption, distinct from explanatory body text."""

    def __init__(self, text):
        super().__init__(text, style=TITLE_STYLE)


def section(title, *, actions=None):
    return HSplit(
        [
            (VSplit([Heading(title), actions], padding=1, height=1)
             if actions is not None else Heading(title)),
            Window(height=RULE_HEIGHT, char=RULE, style=ThemeSelector.DIVIDER.style),
        ]
    )
