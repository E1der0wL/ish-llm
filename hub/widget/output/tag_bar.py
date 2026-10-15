"""Shared tag navigation; engine outputs supply entries and their actions."""

from dataclasses import dataclass

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth

from ..sidebar import SidebarItem, SidebarList
from ..clipboard import copy_text
from ..controls import Button
from ...ui.output.labels import ellipsize
from ...ui.input.registry import ShortcutRegistry


@dataclass(frozen=True, slots=True)
class OutputTag:
    id: str
    title: str
    position: int
    icon: str = ""
    raw: str | None = None
    indent: int = 0
    margin_top: int = 0


class TagBar:
    def __init__(self, output, view):
        self.output = output
        self.view, self.visible, self.selected = view, False, ""
        self.tracking = True
        registry = ShortcutRegistry()
        registry.add(["escape"], "ESC", "close", lambda e: self.close())
        registry.add(["tab", " ", "enter"], "Tab/Space/Enter", "expand", lambda e: self.expand())
        registry.add(["up", "down"], "↑↓", "select", lambda e: self.move(-1 if e.key_sequence[-1].key == "up" else 1))
        registry.add(["c"], "c", "copy", lambda e: e.app.create_background_task(self.copy()))
        self.control = SidebarList(self.items, lambda: self.selected, self.select,
                                   key_bindings=registry.bindings)
        self.control.hub_shortcuts = registry
        self.control.modal = True
        self.window = Window(self.control, wrap_lines=False, style="class:hub.sidebar")
        self.collapse = Button(lambda: view.icons.collapse, handler=self.close, width=3)
        self.collapse.control.hub_shortcuts.add(["escape"], "ESC", "close", lambda e: self.close())
        self.heading = Window(FormattedTextControl(lambda: " " + view.t("tags_title")),
                              height=1, style="class:hub.accent bold")
        self.header = VSplit([self.heading, self.collapse], height=1)
        self.container = ConditionalContainer(VSplit([
            Window(width=1, char="│", style="class:hub.divider"),
            HSplit([self.header, self.window])],
            width=self.width),
            Condition(lambda: self.visible))

    def width(self):
        available = self.output.available_width
        return min(max(1, available - 1), max(14, min(32, available // 3)))

    def entries(self):
        return sorted(self.output.tag_entries(), key=lambda item: item.position)

    def track_position(self):
        self.tracking = True
        entries = self.entries()
        self.selected = min(entries, key=lambda item: abs(item.position - self.output.tag_position())).id if entries else ""

    def items(self):
        entries = self.entries()
        if self.tracking or self.selected not in {item.id for item in entries}:
            position = self.output.tag_position()
            self.selected = min(entries, key=lambda item: abs(item.position - position)).id if entries else ""
        # Divider, selection marker and surrounding spaces occupy four cells.
        width = max(1, self.width() - 4)
        items = []
        for entry in entries:
            prefix = " " * entry.indent + (entry.icon + "  " if entry.icon else "")
            items.append(SidebarItem(entry.id, prefix + ellipsize(entry.title, width - get_cwidth(prefix)),
                                     margin_top=entry.margin_top))
        return items or [SidebarItem("", self.view.t("tags_empty"))]

    def open(self):
        self.view.composer.buffer.cancel_completion()
        self.visible = True
        self.track_position()
        get_app().layout.focus(self.control)
        get_app().invalidate()

    def close(self):
        self.visible = False
        self.view._focus_composer()
        get_app().invalidate()

    def select(self, identifier):
        self.selected = identifier
        self.tracking = False
        item = next((item for item in self.entries() if item.id == identifier), None)
        if item:
            self.output.select_tag(item)
        self.tracking = False
        get_app().invalidate()

    def move(self, direction):
        items = self.items()
        index = next((index for index, item in enumerate(items) if item.key == self.selected), 0)
        self.select(items[max(0, min(len(items) - 1, index + direction))].key)

    def expand(self):
        item = next((item for item in self.entries() if item.id == self.selected), None)
        if item:
            self.output.expand_tag(item)

    async def copy(self):
        item = next((item for item in self.entries() if item.id == self.selected), None)
        if item and item.raw is not None:
            try:
                await copy_text(item.raw)
            except (OSError, RuntimeError) as error:
                self.view.notice = self.view.t("error", error=error)
            get_app().invalidate()
