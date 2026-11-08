"""Shared section heading and rule."""

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.widgets import Label

from ..asset.icon import RULE
from ..config.theme_style import ThemeSelector, ThemeStyle
from .mouse import hover_style

TITLE_STYLE = ThemeStyle.joined(ThemeSelector.ACCENT.style, ThemeStyle.BOLD)
RULE_HEIGHT = 1


class Heading(Label):
    """A category or field caption, distinct from explanatory body text."""

    def __init__(self, text):
        super().__init__(text, style=TITLE_STYLE)


def section(title, *, actions=None, drag_handler=None, hover_rule=False):
    heading = Heading(title)
    rule = Window(height=RULE_HEIGHT, char=RULE, style=ThemeSelector.DIVIDER.style)
    if hover_rule:
        hover_style(rule)
    if drag_handler is not None:
        heading.window.drag_handler = rule.drag_handler = drag_handler
    return HSplit(
        [
            (VSplit([heading, actions], padding=1, height=1)
             if actions is not None else heading),
            rule,
        ]
    )
