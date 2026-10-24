"""Shared section heading and rule."""

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.widgets import Label

from ..asset.icon import RULE
from ..config.theme_style import ThemeSelector, ThemeStyle

TITLE_STYLE = ThemeStyle.joined(ThemeSelector.ACCENT.style, ThemeStyle.BOLD)
RULE_HEIGHT = 1


def section(title):
    return HSplit(
        [
            Label(title, style=TITLE_STYLE),
            Window(height=RULE_HEIGHT, char=RULE, style=ThemeSelector.DIVIDER.style),
        ]
    )
