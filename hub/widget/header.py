"""The same title, inset and style on every Hub page."""

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from ..asset.icon import NERD


class HeaderBar(Window):
    def __init__(self, title, *, icons=lambda: NERD):
        self.title, self.icons = title, icons
        super().__init__(FormattedTextControl(self.fragments), height=1, style="class:hub.header")

    def fragments(self):
        return [("", f" {self.icons().chat}  " + self.title())]
