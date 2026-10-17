"""The same title, inset and style on every Hub page."""

from prompt_toolkit.layout import Window, VSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from ..asset.icon import NERD


class HeaderBar(Window):
    def __init__(self, title, *, icons=lambda: NERD):
        self.title, self.icons = title, icons
        super().__init__(FormattedTextControl(self.fragments), height=1, style="class:hub.header")

    def fragments(self):
        return [("", f" {self.icons().chat}  " + self.title())]


class HeaderActions(VSplit):
    """Page title and ordinary icon buttons share the global key handlers."""

    def __init__(self, title, view):
        from .controls import Button
        from .actions import bind_button_navigation
        self.restart = Button(lambda: view.icons.restart, width=3, handler=view.restart)
        self.minimize = Button(lambda: view.icons.minimize, width=3, handler=view.minimize)
        buttons = [self.restart, self.minimize]
        bind_button_navigation(buttons)
        super().__init__([title, VSplit(buttons, padding=1, width=7)], height=1, style="class:hub.header")
