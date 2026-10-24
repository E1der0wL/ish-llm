"""The same title, inset and style on every Hub page."""

from prompt_toolkit.layout import Window, VSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from ..asset.icon import NERD
from ..config.theme_style import ThemeSelector


class HeaderBar(Window):
    HEIGHT = 1
    EMPTY_STYLE = ""
    ICON_FRAGMENT = " {icon}  "

    def __init__(self, title, *, icons=lambda: NERD):
        self.title, self.icons = title, icons
        super().__init__(
            FormattedTextControl(self.fragments),
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


class HeaderActions(VSplit):
    """Page title and ordinary icon buttons share the global key handlers."""

    BUTTON_WIDTH = 3
    GAP = 1
    WIDTH = 7
    HEIGHT = HeaderBar.HEIGHT

    def __init__(self, title, view):
        from .controls import Button
        from .actions import bind_button_navigation

        self.restart = Button(
            lambda: view.icons.restart, width=self.BUTTON_WIDTH, handler=view.restart
        )
        self.minimize = Button(
            lambda: view.icons.minimize, width=self.BUTTON_WIDTH, handler=view.minimize
        )
        buttons = [self.restart, self.minimize]
        bind_button_navigation(buttons)
        super().__init__(
            [title, VSplit(buttons, padding=self.GAP, width=self.WIDTH)],
            height=self.HEIGHT,
            style=ThemeSelector.HEADER.style,
        )
