"""Session participants share the output sidebar and its focus boundary."""

from typing import TYPE_CHECKING
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, VSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from .tag_bar import TagBar
from ..sidebar import SidebarItem
from ..controls import Button, RadioList, TextArea
from ..dialog import Dialog, PopupSize
from ...locales.keys import MessageKey
from ...components import ManagedComponents
from ...ui.input.registry import ShortcutRegistry
from ...ui.input.keys import KeyName, ShortcutLabel, ShortcutAction
from ...ui.output.labels import ellipsize
from ...backend.project.participant_groups import ParticipantAction

if TYPE_CHECKING:
    from .engine import EngineOutput
    from ...ui.view import HubView


class ParticipantBar(TagBar):
    INVITING = True
    IDLE = False
    PRESERVE_INPUT = False
    ACCEPTED = True

    def __init__(self, output: "EngineOutput", view: "HubView") -> None:
        super().__init__(output, view)
        self.heading.content = FormattedTextControl(lambda: self.SPACE + view.t(MessageKey.PARTICIPANTS_TITLE))
        registry = ShortcutRegistry()
        registry.add([KeyName.UP, KeyName.DOWN], ShortcutLabel.VERTICAL, ShortcutAction.SELECT,
                     lambda e: self.move(self.PREVIOUS if e.key_sequence[self.PREVIOUS].key == KeyName.UP else self.NEXT))
        registry.add([KeyName.ENTER, KeyName.SPACE], ShortcutLabel.ENTER_SPACE,
                     ShortcutAction.SELECT, lambda e: self.expand())
        registry.add([KeyName.ESCAPE], ShortcutLabel.ESCAPE, ShortcutAction.CLOSE, lambda e: self.close())
        registry.add([KeyName.TAB], ShortcutLabel.TAB, ShortcutAction.SELECT,
                     lambda e: e.app.layout.focus(self.add_button))
        self.control.key_bindings = registry.bindings
        registry.attach(self.control)
        self.add_button = Button(lambda: view.t(MessageKey.PARTICIPANTS_ADD), handler=self.invite)
        self.group_button = Button(lambda: view.t(MessageKey.PARTICIPANTS_GROUP_ADD), handler=self.add_group)
        for button in (self.add_button, self.group_button):
            button.window.width = Dimension(min=self.MIN_WIDTH, preferred=button.width)
        self.contents.children.append(VSplit([self.add_button, self.group_button], padding=1))
        for button in (self.add_button, self.group_button, self.collapse):
            keys = ShortcutRegistry.require(button.control)
            keys.add([KeyName.TAB, KeyName.SHIFT_TAB, KeyName.UP], ShortcutLabel.TAB,
                     ShortcutAction.SELECT, lambda e: e.app.layout.focus(self.control))
            keys.add([KeyName.ESCAPE], ShortcutLabel.ESCAPE, ShortcutAction.CLOSE,
                     lambda e: self.close())
        from ..actions import bind_button_navigation
        bind_button_navigation([self.add_button, self.group_button])

    def track_position(self) -> None:
        ids = (*self.view.participant_ids, *(group.id for group in self.view.participant_groups))
        if self.selected not in ids:
            self.selected = ids[self.FIRST] if ids else self.UNSELECTED

    def items(self) -> list[SidebarItem]:
        self.track_position()
        width = max(self.MIN_WIDTH, self.width() - self.RESERVED_CELLS)
        rows, grouped = [], set()
        def item(identifier, title, indent=""):
            marker = self.view.icons.recipient + self.SPACE if self.view.recipient_id == identifier else ""
            return SidebarItem(identifier, ellipsize(indent + marker + title, width))
        for group in self.view.participant_groups:
            rows.append(item(group.id, group.title))
            for identifier in group.members:
                grouped.add(identifier)
                rows.append(item(identifier, self.view.agent_titles.get(identifier, identifier), "  "))
        rows.extend(item(identifier, self.view.agent_titles.get(identifier, identifier))
                    for identifier in self.view.participant_ids if identifier not in grouped)
        return rows or [SidebarItem(self.UNSELECTED, self.view.t(MessageKey.PARTICIPANTS_EMPTY))]

    def select(self, identifier: str) -> None:
        self.selected = identifier
        get_app().invalidate()

    def expand(self) -> None:
        identifier = self.selected
        group = next((group for group in self.view.participant_groups if group.id == identifier), None)
        if group:
            actions = [(MessageKey.PARTICIPANTS_TARGET, lambda: self.target(identifier)),
                       (MessageKey.PARTICIPANTS_GROUP_DELETE, lambda: self.change(ParticipantAction.DELETE_GROUP, identifier))]
        elif identifier in self.view.participant_ids:
            actions = [(MessageKey.DEFINITION_EDIT, lambda: self.edit_agent(identifier)),
                       (MessageKey.PARTICIPANTS_GROUP_MOVE, lambda: self.move_group(identifier)),
                       (MessageKey.PARTICIPANTS_TARGET, lambda: self.target(identifier)),
                       (MessageKey.PARTICIPANTS_EXPEL, lambda: self.change(ParticipantAction.EXPEL, identifier))]
        else:
            return
        self.choose([(index, self.view.t(key)) for index, (key, _) in enumerate(actions)],
                    lambda index: actions[index][1]())

    def choose(self, choices, accept):
        selection = RadioList(choices)
        def confirm(event):
            value = selection.values[selection._selected_index][0]
            self.view.dialogs.close(accepted=self.ACCEPTED)
            accept(value)
        ShortcutRegistry.require(selection.control).add(
            [KeyName.ENTER, KeyName.SPACE], ShortcutLabel.ENTER_SPACE,
            ShortcutAction.CONFIRM, confirm)
        self.view.open_dialog(self.view.t(MessageKey.PARTICIPANTS_TITLE), selection,
                              lambda: accept(selection.current_value), selection,
                              size=PopupSize.CUSTOM)

    def target(self, identifier):
        self.view.recipient_id = "" if self.view.recipient_id == identifier else identifier
        if identifier in self.view.participant_ids:
            self.view.agent_id = identifier
        get_app().invalidate()

    def edit_agent(self, identifier):
        if self.view.on_component:
            self.view.on_component(ManagedComponents.AGENTS.tool_prefix, "",
                                   consume_input=False, edit_identifier=identifier)

    def change(self, action, identifier="", value=""):
        if self.view.on_change_participants and not self.view.no_sessions:
            self.view.on_change_participants(action, identifier, value, lambda _: get_app().invalidate())

    def add_group(self):
        if self.view.no_sessions:
            self.view.notice = self.view.t(MessageKey.SESSION_REQUIRED)
            return
        name = TextArea(multiline=False, height=1)
        self.view.open_dialog(self.view.t(MessageKey.PARTICIPANTS_GROUP_ADD), name,
            lambda: self.change(ParticipantAction.ADD_GROUP, value=name.text.strip()), name,
            validate=lambda: bool(name.text.strip()))

    def move_group(self, identifier):
        choices = [("", self.view.t(MessageKey.PARTICIPANTS_UNGROUPED)),
                   *((group.id, group.title) for group in self.view.participant_groups)]
        self.choose(choices, lambda group: self.change(ParticipantAction.MOVE, identifier, group))

    def invite(self) -> None:
        view = self.view
        if view.no_sessions or not view.on_invite_agent:
            view.notice = view.t(MessageKey.SESSION_REQUIRED)
            return
        choices = [(identifier, view.agent_titles.get(identifier, identifier))
                   for identifier in view.agents if identifier not in view.participant_ids]
        selection = RadioList(choices) if choices else self.ABSENT
        busy = self.IDLE

        def manage():
            view.dialogs.close()
            if view.on_component:
                view.on_component(
                    ManagedComponents.AGENTS.tool_prefix, "",
                    consume_input=self.PRESERVE_INPUT,
                )

        def accept():
            nonlocal busy
            if busy or selection is self.ABSENT:
                return
            busy = self.INVITING
            def done(result):
                nonlocal busy
                busy = self.IDLE
                if result is not self.ABSENT and view._dialog is popup:
                    view.dialogs.close(accepted=self.ACCEPTED)
            view.on_invite_agent(selection.current_value, done)

        buttons = ([Button(view.t(MessageKey.PARTICIPANTS_INVITE), handler=accept)] if choices else [])
        buttons += [Button(view.t(MessageKey.PARTICIPANTS_MANAGE), handler=manage),
                    Button(view.t(MessageKey.HISTORY_CLOSE), handler=view.close_dialog)]
        popup = Dialog(title=view.t(MessageKey.PARTICIPANTS_ADD),
                       size=PopupSize.MEDIUM if choices else PopupSize.SMALL,
                       body=HSplit([selection or Label(view.t(MessageKey.PARTICIPANTS_NO_AVAILABLE))]),
                       buttons=buttons)
        view.dialogs.show(popup, selection or buttons[self.FIRST])
