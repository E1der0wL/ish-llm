"""Common field anatomy for text, finite choices and Engine selectors."""

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.widgets import Label

from ...config.theme_style import ThemeSelector
from ..section import Heading


class FieldLayout(HSplit):
    GAP = 1

    def __init__(self, title, control, *, description="", type_label="", notes=()):
        super().__init__([
            Heading(title),
            *[Label(text, style=ThemeSelector.MUTED.style)
              for text in (description, type_label) if text],
            control,
            *[Label(note, style=ThemeSelector.MUTED.style) for note in notes],
            Window(height=self.GAP),
        ])
