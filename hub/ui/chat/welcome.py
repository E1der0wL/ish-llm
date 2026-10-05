"""A non-focusable welcome screen for projects without sessions."""

from functools import lru_cache

from prompt_toolkit.layout import UIContent, UIControl, Window
from prompt_toolkit.utils import get_cwidth

from ..output.registry import literal_lines


@lru_cache(maxsize=12)
def logo(width: int) -> tuple[str, ...]:
    from pyfiglet import Figlet
    text = Figlet(font="small", width=max(1, width)).renderText("HUB")
    rows = text.rstrip("\n").splitlines()
    return tuple(rows) if width >= 20 else ("HUB",)


class WelcomeControl(UIControl):
    def __init__(self, view):
        self.view = view

    def create_content(self, width, height):
        view = self.view
        content = [[("class:hub.accent", row)] for row in logo(width)]
        content += [[("", "")]]
        for key in ("welcome_title", "welcome_hint", "welcome_keys"):
            content += literal_lines(view.t(key), width, "class:hub.muted")
        top = max(0, (height - len(content)) // 2)
        lines = [[("", "")]] * top
        for row in content:
            length = sum(get_cwidth(text) for _, text in row)
            lines.append([("", " " * max(0, (width - length) // 2)), *row])
        return UIContent(get_line=lambda i: lines[i], line_count=len(lines), show_cursor=False)


def welcome_window(view):
    return Window(WelcomeControl(view), wrap_lines=False)
