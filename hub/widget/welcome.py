"""A non-focusable welcome screen for projects without sessions."""

from hub.locales.keys import MessageKey

from functools import lru_cache

from prompt_toolkit.layout import UIContent, UIControl, Window
from prompt_toolkit.utils import get_cwidth

from ..ui.output.registry import literal_lines
from ..config.theme_style import ThemeSelector


class WelcomeLogo:
    CACHE_SIZE = 12
    FONT = "small"
    MIN_COLUMNS = 1
    TITLE = "HUB"
    NEWLINE = "\n"
    MIN_LOGO_COLUMNS = 20


@lru_cache(maxsize=WelcomeLogo.CACHE_SIZE)
def logo(width: int) -> tuple[str, ...]:
    from pyfiglet import Figlet

    text = Figlet(
        font=WelcomeLogo.FONT, width=max(WelcomeLogo.MIN_COLUMNS, width)
    ).renderText(WelcomeLogo.TITLE)
    rows = text.rstrip(WelcomeLogo.NEWLINE).splitlines()
    return (
        tuple(rows) if width >= WelcomeLogo.MIN_LOGO_COLUMNS else (WelcomeLogo.TITLE,)
    )


class WelcomeControl(UIControl):
    EMPTY = ""
    SPACE = " "
    SHOW_CURSOR = False
    WRAP_LINES = False
    MESSAGES = (
        MessageKey.WELCOME_TITLE,
        MessageKey.WELCOME_HINT,
        MessageKey.WELCOME_KEYS,
    )

    def __init__(self, view):
        self.view = view

    def create_content(self, width, height):
        view = self.view
        content = [[(ThemeSelector.ACCENT.style, row)] for row in logo(width)]
        content += [[(self.EMPTY, self.EMPTY)]]
        for key in self.MESSAGES:
            content += literal_lines(view.t(key), width, ThemeSelector.MUTED.style)
        top = max(0, (height - len(content)) // 2)
        lines = [[(self.EMPTY, self.EMPTY)]] * top
        for row in content:
            length = sum(get_cwidth(text) for _, text in row)
            lines.append(
                [(self.EMPTY, self.SPACE * max(0, (width - length) // 2)), *row]
            )
        return UIContent(
            get_line=lambda i: lines[i],
            line_count=len(lines),
            show_cursor=self.SHOW_CURSOR,
        )


def welcome_window(view):
    return Window(WelcomeControl(view), wrap_lines=WelcomeControl.WRAP_LINES)
