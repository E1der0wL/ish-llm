"""Shared selection-on-navigation list for sessions and settings."""

from dataclasses import dataclass

from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType

from ..asset import icon


@dataclass(frozen=True, slots=True)
class SidebarItem:
    key: str
    title: str
    detail: str = ""


class SidebarList(FormattedTextControl):
    def __init__(self, items, selected, on_select, *, key_bindings=None):
        self.items, self.selected, self.on_select = items, selected, on_select
        super().__init__(self.fragments, focusable=True, show_cursor=False,
                         key_bindings=key_bindings, get_cursor_position=self.cursor_position)

    def cursor_position(self):
        line = 0
        for item in self.items():
            if item.key == self.selected():
                return Point(x=0, y=line)
            line += 3 if item.detail else 1
        return Point(x=0, y=0)

    def fragments(self):
        fragments = []
        for item in self.items():
            def select(event, key=item.key):
                if event.event_type == MouseEventType.MOUSE_UP:
                    get_app().layout.focus(self)
                    self.on_select(key)
            selected = item.key == self.selected()
            fragments.append(("class:hub.selected" if selected else "class:hub.sidebar",
                              f" {icon.SELECTED if selected else ' '} {item.title}\n", select))
            if item.detail:
                fragments.append(("class:hub.muted", f"   {item.detail}\n\n", select))
        if fragments:
            style, text, handler = fragments[-1]
            fragments[-1] = (style, text.rstrip("\n"), handler)
        return fragments
