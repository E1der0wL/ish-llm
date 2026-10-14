"""Navigable index into the transcript's rendered output objects."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, VSplit, Window
from prompt_toolkit.utils import get_cwidth

from ..sidebar import SidebarItem, SidebarList
from ..clipboard import copy_text
from ..output_inspector import SearchableOutputViewport
from ...ui.output import OutputUIContext
from ...ui.output.labels import ellipsize
from ...config.view_state import ReadingPosition
from ...ui.input.registry import ShortcutRegistry


class TagBar:
    def __init__(self, view):
        self.view, self.visible, self.selected = view, False, ""
        registry = ShortcutRegistry()
        registry.add(["c-t"], "Ctrl+T", "close", lambda e: self.close())
        registry.add(["escape"], "ESC", "close", lambda e: self.close())
        registry.add(["tab", " ", "enter"], "Tab/Space/Enter", "expand", lambda e: self.expand())
        registry.add(["up", "down"], "↑↓", "select", lambda e: self.move(-1 if e.key_sequence[-1].key == "up" else 1))
        registry.add(["c"], "c", "copy", lambda e: e.app.create_background_task(self.copy()))
        self.control = SidebarList(self.items, lambda: self.selected, self.select,
                                   key_bindings=registry.bindings)
        self.control.hub_shortcuts = registry
        self.control.modal = True
        self.container = ConditionalContainer(VSplit([
            Window(width=1, char="│", style="class:hub.divider"),
            Window(self.control, wrap_lines=False, style="class:hub.sidebar")],
            width=self.width),
            Condition(lambda: self.visible))

    def width(self):
        return max(14, min(32, self.view._columns() // 3))

    def entries(self):
        control = self.view.transcript.control
        anchors = dict(control._anchors)
        entries = [(item.id, item.title, item.icon, item.start_line) for item in control.objects]
        for index, message in enumerate(control.messages):
            identifier = message.id or f"index:{index}"
            if message.role == "user" and identifier in anchors:
                entries.append((identifier, message.text, "", anchors[identifier]))
        return sorted(entries, key=lambda item: item[3])

    def items(self):
        entries = self.entries()
        if self.selected not in {item[0] for item in entries}:
            self.selected = entries[0][0] if entries else ""
        # Divider, selection marker and surrounding spaces occupy four cells.
        width = max(1, self.width() - 4)
        objects = {item.id for item in self.view.transcript.control.objects}
        items = []
        for identifier, title, glyph, _ in entries:
            is_request = identifier not in objects
            prefix = "" if is_request else " " + (glyph + " " if glyph else "")
            items.append(SidebarItem(identifier, prefix + ellipsize(title, width - get_cwidth(prefix)),
                                     margin_top=1 if is_request else 0))
        return items or [SidebarItem("", self.view.t("tags_empty"))]

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
        item = next((item for item in self.entries() if item[0] == identifier), None)
        if item:
            self.view.transcript.control.restore_position(ReadingPosition(identifier, 0, item[3]))
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
        context = OutputUIContext(view.t, view._rows, view._columns, view.close_dialog,
                                  lambda obj: SearchableOutputViewport(obj, view), view.render_worker)
        popup = view.output_renderers.create_interaction(item, context)
        view.dialogs.show(popup, popup.receiver)

    async def copy(self):
        item = next((item for item in self.view.transcript.control.objects if item.id == self.selected), None)
        if item:
            try:
                self.view.notice = self.view.t(await copy_text(item.raw))
            except (OSError, RuntimeError) as error:
                self.view.notice = self.view.t("error", error=error)
            get_app().invalidate()
