"""Shared selection-on-navigation list for sessions and settings."""

from dataclasses import dataclass

from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType, MouseButton

from ..asset import icon
from .section import section


class SidebarHeading(HSplit):
    """Keep section titles and rules identical across pages and focus changes."""

    def __init__(self, title):
        super().__init__([section(title)], style="class:hub.sidebar-heading")


@dataclass(frozen=True, slots=True)
class SidebarItem:
    key: str
    title: str
    detail: str = ""
    margin_top: int = 0


class SidebarList(FormattedTextControl):
    def __init__(self, items, selected, on_select, *, key_bindings=None, on_activate=None):
        self.items, self.selected, self.on_select = items, selected, on_select
        self.on_activate = on_activate
        super().__init__(self.fragments, focusable=True, show_cursor=False,
                         key_bindings=key_bindings, get_cursor_position=self.cursor_position)

    def cursor_position(self):
        line = 0
        for item in self.items():
            line += item.margin_top
            if item.key == self.selected():
                return Point(x=0, y=line)
            line += 3 if item.detail else 1
        return Point(x=0, y=0)

    def mouse_handler(self, event):
        # Handle the entire row, including whitespace beyond its title.
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            row = 0
            for item in self.items():
                row += item.margin_top
                height = 3 if item.detail else 1
                if row <= event.position.y < row + height:
                    get_app().layout.focus(self)
                    self.on_select(item.key)
                    if self.on_activate:
                        self.on_activate()
                    return None
                row += height
        return NotImplemented

    def fragments(self):
        fragments = []
        for item in self.items():
            if item.margin_top:
                fragments.append(("class:hub.sidebar", "\n" * item.margin_top))
            selected = item.key == self.selected()
            fragments.append(("class:hub.selected" if selected else "class:hub.sidebar",
                              f" {icon.SELECTED if selected else ' '} {item.title}\n"))
            if item.detail:
                fragments.append(("class:hub.muted", f"   {item.detail}\n\n"))
        if fragments:
            style, text = fragments[-1]
            fragments[-1] = (style, text.rstrip("\n"))
        return fragments
