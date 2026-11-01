"""Session membership and groups share one popup and public Hub callbacks."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import to_filter
from prompt_toolkit.layout import DynamicContainer, HSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ...backend.project.participant_groups import ParticipantAction
from ...components import ManagedComponents
from ...config.theme_style import ThemeSelector
from ...locales.keys import MessageKey
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ...ui.input.registry import ShortcutRegistry
from ..controls import Button, CheckboxList, ScrollbarMargin, TextArea
from ..dialog import Dialog, PopupSize
from ..section import Heading
from ..sidebar import SidebarItem, SidebarList


class ParticipantManager(Dialog):
    """Only immutable view observations feed the lists; no reads during paint."""

    def __init__(self, view):
        self.view = view
        self.busy = False
        self.status = ""
        self._signature = None
        self.agents = CheckboxList([("", "")], default_values=[])
        self.agents.window.dont_extend_width = to_filter(False)
        self.agents.window.dont_extend_height = to_filter(False)
        self.agents.window.height = Dimension(min=1, weight=2)
        self.selected_group = ""
        self.group_items = []
        self.groups = SidebarList(lambda: self.group_items, lambda: self.selected_group,
                                  self.select_group)
        registry = ShortcutRegistry().attach(self.groups)
        registry.add([KeyName.UP, KeyName.DOWN], ShortcutLabel.VERTICAL, ShortcutAction.SELECT,
                     lambda e: self.groups.move(-1 if e.key_sequence[-1].key == KeyName.UP else 1))
        self.groups.key_bindings = registry.bindings
        self.group_window = Window(self.groups, height=Dimension(min=1, weight=1),
                                   style=ThemeSelector.POPUP_LIST.style,
                                   right_margins=[ScrollbarMargin(display_arrows=True)])
        self.content = HSplit([
            Heading(view.t(MessageKey.PARTICIPANTS_AGENTS)),
            DynamicContainer(lambda: self.agents if view.agents else
                             Label(view.t(MessageKey.PARTICIPANTS_NO_AVAILABLE))),
            Window(height=1),
            Heading(view.t(MessageKey.PARTICIPANTS_GROUPS)),
            self.group_window,
        ])
        self.buttons = [
            Button(view.t(key), handler=handler)
            for key, handler in (
                (MessageKey.PARTICIPANTS_INVITE, self.invite),
                (MessageKey.DEFINITION_EDIT, self.manage),
                (MessageKey.PARTICIPANTS_EXPEL, self.expel),
                (MessageKey.PARTICIPANTS_GROUP_MOVE, self.move),
                (MessageKey.PARTICIPANTS_GROUP_ADD, self.add_group),
                (MessageKey.PARTICIPANTS_GROUP_DELETE, self.delete_group),
                (MessageKey.HISTORY_CLOSE, self.close),
            )
        ]
        super().__init__(title=view.t(MessageKey.PARTICIPANTS_TITLE),
                         body=DynamicContainer(self.refresh), buttons=self.buttons,
                         size=PopupSize.MEDIUM,
                         status_bar=view.progress.container(lambda: self.status, surface=self))
        self.on_close = self.close
        self.refresh()

    @staticmethod
    def _update_list(choices, values):
        highlighted = choices.values[choices._selected_index][0]
        identifiers = [identifier for identifier, _ in values]
        choices.values = values
        choices._selected_index = identifiers.index(highlighted) if highlighted in identifiers else 0
        choices.current_values = [value for value in choices.current_values if value in identifiers]

    def refresh(self):
        view = self.view
        signature = (tuple((identifier, view.agent_titles.get(identifier, identifier))
                           for identifier in view.agents),
                     tuple(view.participant_ids), tuple(view.participant_groups))
        if signature != self._signature:
            self._signature = signature
            suffix = view.t(MessageKey.PARTICIPANTS_JOINED)
            self._update_list(self.agents, [
                (identifier, title + (" • " + suffix if identifier in view.participant_ids else ""))
                for identifier, title in signature[0]
            ] or [("", "")])
            grouped = {member for group in view.participant_groups for member in group.members}
            groups = [("", view.t(MessageKey.PARTICIPANTS_UNGROUPED),
                       tuple(member for member in view.participant_ids if member not in grouped)),
                      *((group.id, group.title, group.members) for group in view.participant_groups)]
            self.group_items = []
            for identifier, title, members in groups:
                # Sidebar details already end with one empty row. Empty groups
                # need the same separation, not a second spacer after details.
                margin = int(bool(self.group_items) and not self.group_items[-1].detail)
                self.group_items.append(SidebarItem(
                    identifier, f"{title} ({len(members)})",
                    " • ".join(view.agent_titles.get(member, member) for member in members),
                    margin_top=margin,
                ))
            if self.selected_group not in {item.key for item in self.group_items}:
                self.selected_group = ""
        return self.content

    def select_group(self, identifier):
        self.selected_group = identifier
        get_app().invalidate()

    def selected(self, *, invited=False):
        selected = tuple(identifier for identifier in self.agents.current_values if identifier)
        if not selected:
            self.status = self.view.t(MessageKey.PARTICIPANTS_SELECT)
            return ()
        if invited and not set(selected).issubset(self.view.participant_ids):
            self.status = self.view.t(MessageKey.PARTICIPANTS_INVITE_FIRST)
            return ()
        return selected

    def call(self, callback):
        if self.busy:
            return
        self.busy = True
        token = self.view.progress.start(self.view.t(MessageKey.PARTICIPANTS_UPDATING))

        def done(result):
            self.view.progress.finish(token)
            self.busy = False
            self.status = self.view.t(MessageKey.MANAGER_SAVED if result is not None
                                      else MessageKey.TOOLS_FAILED)
            self.refresh()
            get_app().invalidate()

        try:
            callback(done)
        except Exception:
            self.view.progress.finish(token)
            self.busy = False
            raise

    def invite(self):
        if self.busy or not self.view.on_invite_agent:
            return
        selected = self.selected()
        if selected:
            self.call(lambda done: self.view.on_invite_agent(selected, done))

    def change(self, action, identifier="", value=""):
        if not self.busy and self.view.on_change_participants:
            self.call(lambda done: self.view.on_change_participants(action, identifier, value, done))

    def move(self):
        selected = self.selected(invited=True)
        if selected:
            self.change(ParticipantAction.MOVE, selected, self.selected_group)

    def expel(self):
        selected = self.selected(invited=True)
        if selected:
            self.change(ParticipantAction.EXPEL, selected)

    def add_group(self):
        if self.busy:
            return
        view = self.view
        name = TextArea(multiline=False, height=1)
        focus = get_app().layout.current_control

        def restore():
            view.dialogs.show(self, focus)

        def confirm():
            title = name.text.strip()
            if not title:
                popup.status = view.t(MessageKey.PARTICIPANTS_GROUP_NAME_REQUIRED)
                get_app().layout.focus(name)
                get_app().invalidate()
                return
            restore()
            self.change(ParticipantAction.ADD_GROUP, value=title)

        popup = Dialog(title=view.t(MessageKey.PARTICIPANTS_GROUP_ADD),
                       body=HSplit([Heading(view.t(MessageKey.PARTICIPANTS_GROUP_NAME)), name]),
                       buttons=[Button(view.t(MessageKey.CONFIRM), handler=confirm),
                                Button(view.t(MessageKey.CANCEL), handler=restore)],
                       size=PopupSize.SMALL, allow_small_input=True)
        popup.status = ""
        popup.status_bar = view.progress.container(lambda: popup.status, surface=popup)
        popup.on_close = restore
        view.dialogs.show(popup, name)

    def delete_group(self):
        if not self.selected_group:
            self.status = self.view.t(MessageKey.PARTICIPANTS_GROUP_SELECT)
            return
        self.change(ParticipantAction.DELETE_GROUP, self.selected_group)

    def manage(self):
        if self.busy or not self.view.on_component:
            return
        selected = tuple(identifier for identifier in self.agents.current_values if identifier)
        if len(selected) != 1:
            self.status = self.view.t(MessageKey.PARTICIPANTS_EDIT_ONE)
            get_app().invalidate()
            return
        self.busy = True

        def ready(record):
            self.busy = False
            self.status = "" if record is not None else self.view.t(MessageKey.TOOLS_FAILED)
            get_app().invalidate()

        try:
            self.view.on_component(ManagedComponents.AGENTS.tool_prefix, "", consume_input=False,
                                   edit_identifier=selected[0], completed=ready)
        except Exception:
            self.busy = False
            raise

    def close(self):
        if not self.busy:
            self.on_close = None
            self.view.close_dialog()
