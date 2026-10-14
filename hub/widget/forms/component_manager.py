"""Shared import/list/action popup for project component data."""

from datetime import datetime

from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import PathCompleter, ThreadedCompleter
from prompt_toolkit.layout import HSplit, Window, Float, FloatContainer
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ..controls import Button, TextArea, ScrollbarMargin
from ..dialog import Dialog
from ..sidebar import SidebarItem, SidebarList
from ...ui.input.registry import ShortcutRegistry


class ComponentManager(Dialog):
    """Backend callbacks own data; this widget owns selection and pending UI work."""

    def __init__(self, view, request, *, name, directories=False, toggle=False):
        self.view, self.request, self.name = view, request, name
        self.entries, self.selected, self.busy, self.status = [], "", False, ""
        self.path = TextArea(multiline=False, height=1, completer=ThreadedCompleter(PathCompleter(
            only_directories=directories, expanduser=True,
            get_paths=lambda: [str(view.file_root)])), complete_while_typing=True)
        self.path.buffer.accept_handler = self.import_path
        completion = ShortcutRegistry()
        def confirm(event):
            state = self.path.buffer.complete_state
            if state and state.completions:
                self.path.buffer.apply_completion(state.current_completion or state.completions[0])
            else:
                self.path.buffer.start_completion(select_first=False)
        completion.add(["tab"], "Tab", "confirm", confirm,
                       when=lambda: bool(self.path.text.strip() and self.path.buffer.complete_state))
        self.path.control.key_bindings = completion.bindings
        self.path.control.hub_shortcuts = completion
        self.path.control.hub_complete_path = True
        registry = ShortcutRegistry()
        registry.add(["up", "down"], "↑↓", "select", lambda e: self.move(-1 if e.key_sequence[-1].key == "up" else 1))
        registry.add(["enter"], "Enter", "open_directory" if directories else "edit",
                     lambda e: self.open_item(), when=lambda: not self.busy)
        registry.add(["d"], "d", "delete", lambda e: self.delete(), when=lambda: not self.busy)
        if toggle:
            registry.add([" "], "Space", "tools_toggle", lambda e: self.toggle(), when=lambda: not self.busy)
        self.list = SidebarList(self.items, lambda: self.selected, self.select, key_bindings=registry.bindings)
        self.list.hub_shortcuts = registry
        super().__init__(title=view.t(name + "_title"), body=HSplit([
            Label(view.t(name + "_import_hint")), self.path, Window(height=1), Label(view.t("tools_columns")),
            Window(self.list, height=lambda: Dimension.exact(max(2, min(18, view._rows() - 15))),
                   wrap_lines=True, right_margins=[ScrollbarMargin(display_arrows=True)]),
            Label(lambda: self.status or view.t("tools_hint" if name == "tools" else "manager_hint"))]),
            buttons=[Button(view.t("settings_reload"), handler=self.reload),
                     Button(view.t("settings_title"), handler=self.open_settings),
                     Button(view.t("search_close"), handler=view.close_dialog)],
            width=lambda: Dimension(preferred=100, max=max(1, view._columns() - 4)))
        self.container = FloatContainer(self.container, floats=[Float(
            CompletionsMenu(max_height=8, extra_filter=Condition(
                lambda: get_app().layout.current_control is self.path.control)),
            xcursor=True, ycursor=True, attach_to_window=self.path.window, z_index=500)])

    def open_settings(self):
        self.view.close_dialog()
        self.view.settings.open_component(self.name)

    def items(self):
        rows = []
        for item in self.entries:
            title = item.get("label", item["name"])
            if "enabled" in item:
                title = ("[x] " if item["enabled"] else "[ ] ") + title
            modified = item.get("modified")
            try:
                stamp = datetime.fromisoformat(modified).astimezone().strftime("%Y-%m-%d %H:%M") if modified else "—"
            except (ValueError, TypeError):
                stamp = "—"
            if item.get("builtin"):
                stamp = self.view.t("tools_builtin")
            description = item.get("description") or "—"
            rows.append(SidebarItem(item["name"], title, stamp + " · " + description.splitlines()[0]))
        return rows or [SidebarItem("", self.view.t("manager_empty"))]

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
        progress = self.view.progress.start(self.view.t("progress_component", command=f"/{self.name} {action}"))
        def done(result):
            self.view.progress.finish(progress)
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
        state = self.path.buffer.complete_state
        if state and state.current_completion:
            self.path.buffer.apply_completion(state.current_completion)
            return True
        original = self.path.text
        if original.strip():
            before = {item["name"] for item in self.entries}
            def imported(entries):
                self.entries = entries
                self.selected = next((item["name"] for item in entries if item["name"] not in before), self.selected)
                if self.path.text == original:
                    self.path.text = ""
                self.status = self.view.t("tools_imported" if self.name == "tools" else "manager_imported")
            self.call("import", original.strip(), completed=imported)
        return True

    def open_item(self):
        raise NotImplementedError

    def delete(self):
        item = next((item for item in self.entries if item["name"] == self.selected), None)
        if self.busy or item is None or item.get("builtin"):
            return
        def restore(delete=False):
            self.view.dialogs.show(self, self.list)
            if delete:
                self.call("delete", item["name"], item["version"])
        buttons = [Button(self.view.t("confirm"), handler=lambda: restore(True)),
                   Button(self.view.t("cancel"), handler=restore)]
        confirmation = Dialog(title=self.view.t("component_delete"),
            body=Label(self.view.t("tools_delete_confirm" if self.name == "tools" else "manager_delete_confirm", name=item["name"])),
            buttons=buttons)
        confirmation.on_close = restore
        self.view.dialogs.show(confirmation, buttons[1])
