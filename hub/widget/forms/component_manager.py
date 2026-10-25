"""Shared import/list/action popup for project component data."""

from hub.locales.keys import MessageKey, MessagePattern

from datetime import datetime

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ..controls import Button, ScrollbarMargin
from .path_input import PathInput
from ..input_field import InputField
from ..dialog import Dialog
from ..sidebar import SidebarItem, SidebarList
from ...ui.input.registry import ShortcutRegistry


class ComponentManager(Dialog):
    """Backend callbacks own data; this widget owns selection and pending UI work."""

    def __init__(self, view, request, *, name, directories=False, toggle=False, extra_buttons=(), detail_lines=1,
                 button_width=12, component_settings=True):
        self.view, self.request, self.name = view, request, name
        self.component_settings = component_settings
        self.entries, self.selected, self.busy, self.status = [], "", False, ""
        self.path = InputField(input=PathInput(lambda: view.file_root, directories=directories),
                               button_text=view.t(MessageKey.COMPONENT_IMPORT), on_action=lambda: self.import_path(self.path.buffer))
        self.path.buffer.accept_handler = self.import_path
        registry = ShortcutRegistry()
        registry.add(["up", "down"], "↑↓", "select", lambda e: self.move(-1 if e.key_sequence[-1].key == "up" else 1))
        registry.add(["enter"], "Enter", "open_directory" if directories else "edit",
                     lambda e: self.open_item(), when=lambda: not self.busy)
        registry.add(["d"], "d", "delete", lambda e: self.delete(), when=lambda: not self.busy)
        if toggle:
            registry.add([" "], "Space", "tools_toggle", lambda e: self.toggle(), when=lambda: not self.busy)
        self.list = SidebarList(self.items, lambda: self.selected, self.select, key_bindings=registry.bindings)
        registry.attach(self.list)
        super().__init__(title=view.t(MessagePattern.component_title(name)), body=HSplit([
            Label(view.t(MessagePattern.component_import_hint(name))), self.path, Window(height=1), Label(view.t(MessageKey.TOOLS_COLUMNS)),
            Window(self.list, height=lambda: Dimension.exact(max(2, min(18, view._rows() - 14 - detail_lines))),
                   wrap_lines=True, right_margins=[ScrollbarMargin(display_arrows=True)]),
            Label(self.hint)]),
            buttons=[Button(view.t(MessageKey.SETTINGS_RELOAD), handler=self.reload, width=button_width),
                     *([Button(view.t(MessageKey.SETTINGS_TITLE), handler=self.open_settings, width=button_width)]
                       if component_settings else []),
                     *extra_buttons,
                     Button(view.t(MessageKey.SEARCH_CLOSE), handler=view.close_dialog, width=button_width)],
            width=lambda: Dimension(preferred=100, max=max(1, view._columns() - 4)))

    def hint(self):
        return self.status or self.view.t(MessageKey.TOOLS_HINT if self.name == "tools" else MessageKey.MANAGER_HINT)

    def open_settings(self):
        if not self.component_settings:
            raise ValueError("This domain has no Component configuration")
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
                stamp = self.view.t(MessageKey.TOOLS_BUILTIN)
            description = item.get("description") or "—"
            rows.append(SidebarItem(item["name"], title, stamp + " · " + description.splitlines()[0]))
        return rows or [SidebarItem("", self.view.t(MessageKey.MANAGER_EMPTY))]

    def select(self, name):
        self.selected = name
        get_app().invalidate()

    def move(self, direction):
        names = [item["name"] for item in self.entries]
        if names:
            index = names.index(self.selected) if self.selected in names else 0
            self.select(names[max(0, min(len(names) - 1, index + direction))])

    def call(self, action, argument="", version=None, completed=None, *, observe=None):
        if self.busy:
            return
        self.busy = True
        self.status = self.view.t(MessageKey.COMPONENT_LOADING)
        progress = self.view.progress.start(self.view.t(MessageKey.PROGRESS_COMPONENT, command=f"/{self.name} {action}"))
        def done(result):
            self.view.progress.finish(progress)
            self.busy = False
            self.status = "" if result is not None else self.view.t(MessageKey.TOOLS_FAILED)
            if result is not None:
                if completed:
                    completed(result)
                else:
                    self.entries = result
                    if self.selected not in {item["name"] for item in result}:
                        self.selected = result[0]["name"] if result else ""
            get_app().invalidate()
        options = {"progress": lambda update: observe(update, progress)} if observe else {}
        self.request(action, argument, version, done, **options)

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
                self.status = self.view.t(MessageKey.TOOLS_IMPORTED if self.name == "tools" else MessageKey.MANAGER_IMPORTED)
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
        buttons = [Button(self.view.t(MessageKey.CONFIRM), handler=lambda: restore(True)),
                   Button(self.view.t(MessageKey.CANCEL), handler=restore)]
        confirmation = Dialog(title=self.view.t(MessageKey.COMPONENT_DELETE),
            body=Label(self.view.t(MessageKey.TOOLS_DELETE_CONFIRM if self.name == "tools" else MessageKey.MANAGER_DELETE_CONFIRM, name=item["name"])),
            buttons=buttons)
        confirmation.on_close = restore
        self.view.dialogs.show(confirmation, buttons[1])
