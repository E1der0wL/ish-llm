"""The same title, inset and style on every Hub page."""

from prompt_toolkit.layout import Window, VSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from ..asset.icon import NERD
from ..config.theme_style import ThemeSelector, ThemeStyle
from .controls import Button
from .actions import bind_button_navigation


class HeaderButton(Button):
    """Header-owned action; its surface is always the header surface."""
    WIDTH = 3

    def __init__(self, text, handler):
        super().__init__(text, handler=handler, width=self.WIDTH)
        style = self.window.style
        self.window.style = lambda: ThemeStyle.joined(
            style() if callable(style) else style, ThemeSelector.HEADER.style)


class HeaderBar(VSplit):
    HEIGHT = 1
    EMPTY_STYLE = ""
    ICON_FRAGMENT = " {icon}  "

    BUTTON_GAP = 1

    def __init__(self, title, *, icons=lambda: NERD, restart, minimize):
        self.title, self.icons = title, icons
        self.label = Window(FormattedTextControl(self.fragments), height=self.HEIGHT,
                            style=ThemeSelector.HEADER.style)
        self.restart = HeaderButton(lambda: icons().restart, restart)
        self.minimize = HeaderButton(lambda: icons().minimize, minimize)
        buttons = [self.restart, self.minimize]
        bind_button_navigation(buttons)
        super().__init__(
            [self.label, VSplit(buttons, padding=self.BUTTON_GAP,
                               width=len(buttons) * HeaderButton.WIDTH + self.BUTTON_GAP)],
            height=self.HEIGHT,
            style=ThemeSelector.HEADER.style,
        )

    def fragments(self):
        return [
            (
                self.EMPTY_STYLE,
                self.ICON_FRAGMENT.format(icon=self.icons().chat) + self.title(),
            )
        ]
