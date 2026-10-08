"""Navigable index into the transcript's rendered output objects."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, VSplit, Window

from .sidebar import SidebarItem, SidebarList
from .clipboard import copy_text
from .reader import ReadOnlyDialog
from .output_viewport import OutputViewport
from ..config.view_state import ReadingPosition
from ..ui.input.registry import ShortcutRegistry


class TagBar:
    def __init__(self, view):
        self.view, self.visible, self.selected = view, False, ""
        registry = ShortcutRegistry()
        registry.add(["c-t"], "Ctrl+T", "close", lambda e: self.close())
        registry.add(["escape"], "ESC", "close", lambda e: self.close())
        registry.add(["tab", " ", "enter"], "Tab/Space/Enter", "expand", lambda e: self.expand())
        registry.add(["up", "down"], "↑↓", "select", lambda e: self.move(-1 if e.key_sequence[-1].key == "up" else 1))
        registry.add(["c"], "c", "copy", lambda e: e.app.create_background_task(self.copy()))
        self.control = SidebarList(self.items, lambda: self.selected, self.select, key_bindings=registry.bindings)
        self.control.hub_shortcuts = registry
        self.control.modal = True
        self.container = ConditionalContainer(VSplit([
            Window(width=1, char="│", style="class:hub.divider"),
            Window(self.control, wrap_lines=True, style="class:hub.sidebar")],
            width=lambda: max(14, min(32, view._columns() // 3))),
            Condition(lambda: self.visible))

    def items(self):
        objects = self.view.transcript.control.objects
        if objects and self.selected not in {item.id for item in objects}:
            self.selected = objects[0].id
        return [SidebarItem(item.id, f"{index + 1}. {item.title}") for index, item in enumerate(objects)] or [
            SidebarItem("", self.view.t("tags_empty"))]

    def open(self):
        self.view.composer.buffer.cancel_completion()
        self.visible = True
        self.items()
        get_app().layout.focus(self.control)
        self.select(self.selected)

    def close(self):
        self.visible = False
        self.view._focus_composer()
        get_app().invalidate()

    def select(self, identifier):
        self.selected = identifier
        item = next((item for item in self.view.transcript.control.objects if item.id == identifier), None)
        if item:
            self.view.transcript.control.restore_position(ReadingPosition(item.id, 0, item.start_line))
        get_app().invalidate()

    def move(self, direction):
        items = self.items()
        index = next((index for index, item in enumerate(items) if item.key == self.selected), 0)
        self.select(items[max(0, min(len(items) - 1, index + direction))].key)

    def expand(self):
        item = next((item for item in self.view.transcript.control.objects if item.id == self.selected), None)
        if item is None:
            return
        view = self.view
        popup = ReadOnlyDialog(item.title, item.raw, view._rows, view._columns,
                               view.close_dialog, view.t, output=OutputViewport(item, view), full_screen=True)
        view.dialogs.show(popup, popup.receiver)

    async def copy(self):
        item = next((item for item in self.view.transcript.control.objects if item.id == self.selected), None)
        if item:
            try:
                self.view.notice = self.view.t(await copy_text(item.raw))
            except (OSError, RuntimeError) as error:
                self.view.notice = self.view.t("error", error=error)
            get_app().invalidate()
