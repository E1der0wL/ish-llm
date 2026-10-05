"""Shared section heading and rule."""

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.widgets import Label

from ..asset.icon import RULE


def section(title):
    return HSplit([Label(title, style="class:hub.accent bold"),
                   Window(height=1, char=RULE, style="class:hub.divider")])
