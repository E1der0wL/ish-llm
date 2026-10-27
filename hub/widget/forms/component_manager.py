"""Shared import/list/action popup for project component data."""

from hub.locales.keys import MessageKey, MessagePattern
from hub.ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from .manager_values import (
    ManagerView,
    ManagerCallbacks,
)
from hub.backend.project.management_values import (
    ManagementAction,
    ManagementEntry,
)
from hub.components import ManagedComponents


from datetime import datetime

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ..controls import Button, ScrollbarMargin
from .path_input import PathInput
from ..input_field import InputField
from ..dialog import Dialog, PopupSize
from ..sidebar import SidebarItem, SidebarList
from ..actions import ActionBar
from ...ui.input.registry import ShortcutRegistry


class ComponentManager(Dialog):
    """Backend callbacks own data; this widget owns selection and pending UI work."""

    def __init__(
        self,
        view,
        request,
        *,
        name,
        directories=ManagerView.DIRECTORIES,
        toggle=ManagerView.TOGGLE,
        extra_buttons=(),
        detail_lines=ManagerView.DETAIL_LINES,
        button_width=ManagerView.BUTTON_WIDTH,
        component_settings=ManagerView.COMPONENT_SETTINGS
    ):
        self.view, self.request, self.name = view, request, name
        self.component_settings = component_settings
        self.entries, self.selected, self.busy, self.status = (
            [],
            ManagerView.EMPTY,
            ManagerView.IDLE,
            ManagerView.EMPTY,
        )
        self.path = InputField(
            input=PathInput(lambda: view.file_root, directories=directories),
            button_text=view.t(MessageKey.COMPONENT_IMPORT),
            on_action=lambda: self.import_path(self.path.buffer),
        )
        self.path.buffer.accept_handler = self.import_path
        registry = ShortcutRegistry()
        registry.add(
            [KeyName.UP, KeyName.DOWN],
            ShortcutLabel.VERTICAL,
            ShortcutAction.SELECT,
            lambda e: self.move(
                ManagerView.PREVIOUS
                if e.key_sequence[ManagerView.PREVIOUS].key == KeyName.UP
                else ManagerView.NEXT
            ),
        )
        registry.add(
            [KeyName.ENTER],
            ShortcutLabel.ENTER,
            ShortcutAction.OPEN_DIRECTORY if directories else ShortcutAction.EDIT,
            lambda e: self.open_item(),
            when=lambda: not self.busy,
        )
        registry.add(
            [KeyName.CLEAR],
            KeyName.CLEAR,
            ShortcutAction.DELETE,
            lambda e: self.delete(),
            when=lambda: not self.busy,
        )
        if toggle:
            registry.add(
                [KeyName.SPACE],
                ShortcutLabel.SPACE,
                ShortcutAction.TOOLS_TOGGLE,
                lambda e: self.toggle(),
                when=lambda: not self.busy,
            )
        self.list = SidebarList(
            self.items,
            lambda: self.selected,
            self.select,
            key_bindings=registry.bindings,
        )
        registry.attach(self.list)
        self.buttons = [
            *([Button(view.t(MessageKey.SETTINGS_TITLE), handler=self.open_settings,
                      width=button_width)] if component_settings else []),
            *extra_buttons,
            Button(view.t(MessageKey.HELP_TITLE), handler=self.help, width=button_width),
            Button(view.t(MessageKey.SEARCH_CLOSE), handler=view.close_dialog, width=button_width),
        ]
        self.actions = ActionBar(self.buttons, lambda: self.view._columns())
        self.status_line = view.progress.container(self.hint, surface=self)
        super().__init__(
            title=view.t(MessagePattern.component_title(name)),
            body=HSplit(
                [
                    Label(view.t(MessagePattern.component_import_hint(name))),
                    self.path,
                    Window(height=ManagerView.ROW_SPACER),
                    Label(view.t(MessageKey.TOOLS_COLUMNS)),
                    Window(
                        self.list,
                        height=lambda: Dimension(
                            min=1,
                            preferred=max(
                                ManagerView.MINIMUM_LIST_HEIGHT,
                                min(
                                    ManagerView.MAXIMUM_LIST_HEIGHT,
                                    view._rows()
                                    - ManagerView.VERTICAL_CHROME
                                    - detail_lines,
                                ),
                            )
                        ),
                        wrap_lines=ManagerView.WRAP_LINES,
                        right_margins=[
                            ScrollbarMargin(display_arrows=ManagerView.SCROLL_ARROWS)
                        ],
                    ),
                    Window(height=ManagerView.ROW_SPACER),
                    self.actions,
                    self.status_line,
                ]
            ),
            size=PopupSize.MEDIUM,
        )

    def help(self):
        from ..output.reader import ReadOnlyDialog
        from ...components import component_spec

        def close():
            self.view.dialogs.show(self, self.list)

        popup = ReadOnlyDialog(
            self.view.t(MessageKey.HELP_TITLE),
            self.view.t(MessageKey.COMPONENT_HELP, command=component_spec(self.name).tool_prefix)
            + "\n\n" + self.view.t(MessagePattern.component_help(self.name)),
            self.view._rows, self.view._columns, close, self.view.t,
            markdown=True, theme=lambda: self.view.theme,
            render_worker=self.view.render_worker,
        )
        self.view.dialogs.show(popup, popup.receiver)

    def hint(self):
        return self.status

    def open_settings(self):
        if not self.component_settings:
            raise ValueError(ManagerView.NO_SETTINGS)
        self.view.close_dialog()
        self.view.settings.open_component(self.name)

    def items(self):
        rows = []
        for item in self.entries:
            title = ManagementEntry(item).label
            if ManagementEntry(item).has_enabled:
                title = (
                    ManagerView.ENABLED_MARK
                    if ManagementEntry(item).enabled
                    else ManagerView.DISABLED_MARK
                ) + title
            modified = ManagementEntry(item).modified
            try:
                stamp = (
                    datetime.fromisoformat(modified)
                    .astimezone()
                    .strftime(ManagerView.DATE_FORMAT)
                    if modified
                    else ManagerView.UNKNOWN
                )
            except (ValueError, TypeError):
                stamp = ManagerView.UNKNOWN
            if ManagementEntry(item).builtin:
                stamp = self.view.t(MessageKey.TOOLS_BUILTIN)
            description = ManagementEntry(item).description or ManagerView.UNKNOWN
            rows.append(
                SidebarItem(
                    ManagementEntry(item).name,
                    title,
                    stamp
                    + ManagerView.SEPARATOR
                    + description.splitlines()[ManagerView.FIRST],
                )
            )
        return rows or [
            SidebarItem(ManagerView.EMPTY, self.view.t(MessageKey.MANAGER_EMPTY))
        ]

    def select(self, name):
        self.selected = name
        get_app().invalidate()

    def move(self, direction):
        names = [ManagementEntry(item).name for item in self.entries]
        if names:
            index = (
                names.index(self.selected)
                if self.selected in names
                else ManagerView.FIRST
            )
            self.select(
                names[
                    max(
                        ManagerView.FIRST,
                        min(len(names) - ManagerView.NEXT, index + direction),
                    )
                ]
            )

    def call(
        self,
        action,
        argument=ManagerView.EMPTY,
        version=ManagerView.ABSENT,
        completed=ManagerView.ABSENT,
        *,
        observe=ManagerView.ABSENT
    ):
        if self.busy:
            return
        self.busy = ManagerView.BUSY
        self.status = self.view.t(MessageKey.COMPONENT_LOADING)
        progress = self.view.progress.start(
            self.view.t(
                MessageKey.PROGRESS_COMPONENT,
                command=ManagerView.command(self.name, action),
            )
        )

        def done(result):
            self.view.progress.finish(progress)
            self.busy = ManagerView.IDLE
            self.status = (
                ManagerView.EMPTY
                if result is not ManagerView.ABSENT
                else self.view.t(MessageKey.TOOLS_FAILED)
            )
            if result is not ManagerView.ABSENT:
                if completed:
                    completed(result)
                else:
                    self.entries = result
                    if self.selected not in {
                        ManagementEntry(item).name for item in result
                    }:
                        self.selected = (
                            ManagementEntry(result[ManagerView.FIRST]).name
                            if result
                            else ManagerView.EMPTY
                        )
            get_app().invalidate()

        options = ManagerCallbacks.options(observe, progress)
        self.request(action, argument, version, self.view.progress.callback(progress, done), **options)

    def reload(self):
        self.call(ManagementAction.LIST)

    def import_path(self, _):
        state = self.path.buffer.complete_state
        if state and state.current_completion:
            self.path.buffer.apply_completion(state.current_completion)
            return ManagerView.HANDLED
        original = self.path.text
        if original.strip():
            before = {ManagementEntry(item).name for item in self.entries}

            def imported(entries):
                self.entries = entries
                self.selected = next(
                    (
                        ManagementEntry(item).name
                        for item in entries
                        if ManagementEntry(item).name not in before
                    ),
                    self.selected,
                )
                if self.path.text == original:
                    self.path.text = ManagerView.EMPTY
                self.status = self.view.t(
                    MessageKey.TOOLS_IMPORTED
                    if self.name == ManagedComponents.TOOLS.name
                    else MessageKey.MANAGER_IMPORTED
                )

            self.call(ManagementAction.IMPORT, original.strip(), completed=imported)
        return ManagerView.HANDLED

    def open_item(self):
        raise NotImplementedError

    def delete(self):
        item = next(
            (
                item
                for item in self.entries
                if ManagementEntry(item).name == self.selected
            ),
            ManagerView.ABSENT,
        )
        if self.busy or item is ManagerView.ABSENT or ManagementEntry(item).builtin:
            return

        def restore(delete=ManagerView.NOT_CONFIRMED):
            self.view.dialogs.show(self, self.list)
            if delete:
                self.call(
                    ManagementAction.DELETE,
                    ManagementEntry(item).name,
                    ManagementEntry(item).version,
                )

        buttons = [
            Button(
                self.view.t(MessageKey.CONFIRM),
                handler=lambda: restore(ManagerView.CONFIRMED),
            ),
            Button(self.view.t(MessageKey.CANCEL), handler=restore),
        ]
        confirmation = Dialog(
            title=self.view.t(MessageKey.COMPONENT_DELETE),
            body=Label(
                self.view.t(
                    (
                        MessageKey.TOOLS_DELETE_CONFIRM
                        if self.name == ManagedComponents.TOOLS.name
                        else MessageKey.MANAGER_DELETE_CONFIRM
                    ),
                    name=ManagementEntry(item).name,
                )
            ),
            buttons=buttons,
        )
        confirmation.on_close = restore
        self.view.dialogs.show(confirmation, buttons[ManagerView.CANCEL_BUTTON])
