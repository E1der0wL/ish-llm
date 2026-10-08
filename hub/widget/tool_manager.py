"""Tool directory importer and package browser, independent of backend ownership."""

import asyncio
from datetime import datetime

from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from .controls import Button, TextArea, ScrollbarMargin
from .dialog import Dialog
from .sidebar import SidebarItem, SidebarList
from ..ui.input.registry import ShortcutRegistry


class ToolManager(Dialog):
    def __init__(self, view, request):
        self.view, self.request = view, request
        self.entries, self.selected, self.busy, self.status = [], "", False, ""
        self.path = TextArea(multiline=False, height=1, completer=PathCompleter(only_directories=True),
                             complete_while_typing=True)
        self.path.buffer.accept_handler = self.import_path
        registry = ShortcutRegistry()
        registry.add(["up", "down"], "↑↓", "select", lambda e: self.move(-1 if e.key_sequence[-1].key == "up" else 1))
        registry.add(["enter"], "Enter", "open_directory", lambda e: self.open_directory(), when=lambda: not self.busy)
        registry.add(["d"], "d", "delete", lambda e: self.delete(), when=lambda: not self.busy)
        registry.add([" "], "Space", "tools_toggle", lambda e: self.toggle(), when=lambda: not self.busy)
        self.list = SidebarList(self.items, lambda: self.selected, self.select, key_bindings=registry.bindings)
        self.list.hub_shortcuts = registry
        super().__init__(title=view.t("tools_title"), body=HSplit([
            Label(view.t("tools_import_hint")), self.path, Window(height=1), Label(view.t("tools_columns")),
            Window(self.list, height=lambda: Dimension.exact(max(2, min(18, view._rows() - 15))),
                   wrap_lines=True, right_margins=[ScrollbarMargin(display_arrows=True)]),
            Label(lambda: self.status or view.t("tools_hint"))]),
            buttons=[Button(view.t("settings_reload"), handler=self.reload),
                     Button(view.t("search_close"), handler=view.close_dialog)],
            width=lambda: Dimension(preferred=100, max=max(1, view._columns() - 4)))

    def items(self):
        return [SidebarItem(item["name"], ("[x] " if item.get("enabled") else "[ ] ") + item.get("label", item["name"]),
            (self.view.t("tools_builtin") if item.get("builtin") else
             datetime.fromisoformat(item["modified"]).astimezone().strftime("%Y-%m-%d %H:%M")) + " · " +
            (item["description"].splitlines()[0] if item["description"] else "—")) for item in self.entries] or [
            SidebarItem("", self.view.t("tools_empty"))]

    def select(self, name):
        self.selected = name
        get_app().invalidate()

    def move(self, direction):
        names = [item["name"] for item in self.entries]
        if names:
            index = names.index(self.selected) if self.selected in names else 0
            self.select(names[max(0, min(len(names) - 1, index + direction))])

    def call(self, action, argument="", version=None, completed=None):
        if self.busy:
            return
        self.busy = True
        self.status = self.view.t("component_loading")
        def done(result):
            self.busy = False
            self.status = "" if result is not None else self.view.t("tools_failed")
            if result is not None:
                if completed:
                    completed(result)
                else:
                    self.entries = result
                    if self.selected not in {item["name"] for item in result}:
                        self.selected = result[0]["name"] if result else ""
            get_app().invalidate()
        self.request(action, argument, version, done)

    def reload(self):
        self.call("list")

    def import_path(self, _):
        original = self.path.text
        if original.strip():
            def imported(entries):
                self.entries = entries
                self.selected = entries[-1]["name"] if entries else ""
                if self.path.text == original:
                    self.path.text = ""
                self.status = self.view.t("tools_imported")
            self.call("import", original.strip(), completed=imported)
        return True

    def open_directory(self):
        if self.selected.startswith("builtin:"):
            return
        if self.selected:
            self.call("open", self.selected,
                      completed=lambda path: get_app().create_background_task(self.launch(path)))

    async def launch(self, path):
        try:
            process = await asyncio.create_subprocess_exec("xdg-open", path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            code = await process.wait()
            if code:
                raise OSError(self.view.t("tools_explorer_failed", code=code))
        except OSError as error:
            self.status = self.view.t("error", error=error)
        get_app().invalidate()

    def delete(self):
        item = next((item for item in self.entries if item["name"] == self.selected), None)
        if item is None or item.get("builtin"):
            return
        def restore(delete=False):
            self.view.dialogs.show(self, self.list)
            if delete:
                self.call("delete", item["name"], item["version"])
        # Confirmation is a child popup; closing it also restores this manager.
        buttons = [Button(self.view.t("confirm"), handler=lambda: restore(True)),
                   Button(self.view.t("cancel"), handler=restore)]
        confirmation = Dialog(title=self.view.t("component_delete"),
            body=Label(self.view.t("tools_delete_confirm", name=item["name"])),
            buttons=buttons)
        confirmation.on_close = restore
        self.view.dialogs.show(confirmation, buttons[1])

    def toggle(self):
        item = next((item for item in self.entries if item["name"] == self.selected), None)
        if item and item.get("builtin"):
            self.call("toggle", item["name"], item["version"])
