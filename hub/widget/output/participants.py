"""Session participants share the output sidebar and its focus boundary."""

from typing import TYPE_CHECKING
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, to_container
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Label

from .tag_bar import TagBar
from ..sidebar import SidebarItem
from ..controls import Button, RadioList
from ..dialog import Dialog
from ...locales.keys import MessageKey
from ...components import ManagedComponents
from ...backend.project.component_commands import ComponentAction
from ...ui.input.registry import ShortcutRegistry
from ...ui.input.keys import KeyName, ShortcutLabel, ShortcutAction
from ...ui.output.labels import ellipsize

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
        self.contents.children.append(to_container(self.add_button))
        for button in (self.add_button, self.collapse):
            keys = ShortcutRegistry.require(button.control)
            keys.add([KeyName.TAB, KeyName.SHIFT_TAB, KeyName.UP], ShortcutLabel.TAB,
                     ShortcutAction.SELECT, lambda e: e.app.layout.focus(self.control))
            keys.add([KeyName.ESCAPE], ShortcutLabel.ESCAPE, ShortcutAction.CLOSE,
                     lambda e: self.close())

    def track_position(self) -> None:
        ids = self.view.participant_ids
        if self.selected not in ids:
            self.selected = ids[self.FIRST] if ids else self.UNSELECTED

    def items(self) -> list[SidebarItem]:
        self.track_position()
        width = max(self.MIN_WIDTH, self.width() - self.RESERVED_CELLS)
        return [SidebarItem(identifier, ellipsize(
            (self.view.icons.recipient + self.SPACE if self.view.recipient_id == identifier else self.UNSELECTED)
            + self.view.agent_titles.get(identifier, identifier), width))
            for identifier in self.view.participant_ids] or [SidebarItem(self.UNSELECTED, self.view.t(MessageKey.PARTICIPANTS_EMPTY))]

    def select(self, identifier: str) -> None:
        self.selected = identifier
        get_app().invalidate()

    def expand(self) -> None:
        if self.selected in self.view.participant_ids:
            self.view.recipient_id = self.UNSELECTED if self.view.recipient_id == self.selected else self.selected
            if self.view.recipient_id:
                self.view.agent_id = self.view.recipient_id
            get_app().invalidate()

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
                    ManagedComponents.AGENTS.name, ComponentAction.CONFIG.value,
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
                       body=HSplit([selection or Label(view.t(MessageKey.PARTICIPANTS_NO_AVAILABLE))]),
                       buttons=buttons)
        view.dialogs.show(popup, selection or buttons[self.FIRST])
