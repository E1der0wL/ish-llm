"""A nonfocusable color sample, safe while a hex value is being edited."""

import re

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from ..config.theme import HubTheme
from ..config.theme_style import ThemeStyle


class ColorPreview(Window):
    WIDTH = 2
    HEIGHT = 1
    SAMPLE = " " * WIDTH
    UNCOLORED = ""
    SCHEMA_KEY = "x-hub-color"

    def __init__(self, value):
        self.value = value
        super().__init__(
            FormattedTextControl(self.fragments), width=self.WIDTH,
            height=self.HEIGHT, dont_extend_height=True,
        )

    def fragments(self):
        try:
            value = self.value()
        except (ValueError, TypeError):
            value = None
        if (isinstance(value, str) and value != HubTheme.DEFAULT_COLOR
                and re.fullmatch(HubTheme.COLOR_PATTERN, value)):
            return [(ThemeStyle.BACKGROUND_PREFIX + value, self.SAMPLE)]
        return [(self.UNCOLORED, self.SAMPLE)]
