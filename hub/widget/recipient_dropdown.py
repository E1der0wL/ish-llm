"""Composer title menu using the common dropdown and Session recipients."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import to_filter
from prompt_toolkit.utils import get_cwidth

from ..locales.keys import MessageKey
from ..model import SessionIdentity
from .dropdown import DropdownBox


class RecipientDropdown(DropdownBox):
    MIN_WIDTH = 28
    TITLE_PADDING = 2
    TITLE_STYLE = 'class:frame.label'

    def __init__(self, view):
        self.view = view
        self.identity = None
        super().__init__(self.recipients, on_change=self.select,
                         width=self.MIN_WIDTH, icons=lambda: view.icons)
        self.button.window.width = self.title_width
        # The title is mouse-accessible; Ctrl+T enters the menu directly. It
        # does not add another keyboard stop or change the frame's title style.
        self.button.control.focusable = to_filter(False)
        self.button.window.style = self.TITLE_STYLE
        self.button.TEXT_STYLE = self.TITLE_STYLE
        self.style = self.TITLE_STYLE

    def title_width(self):
        return max(self.MIN_WIDTH, get_cwidth(self.caption()) + self.TITLE_PADDING)

    def caption(self):
        return self.view.recipient_title()

    def recipients(self):
        view = self.view
        return [
            (view.NO_AGENT, view.t(MessageKey.PARTICIPANTS_ALL)),
            *((identifier, view.icons.agent + '  ' + view.agent_titles.get(identifier, identifier))
              for identifier in view.participant_ids),
            *((group.id, view.t(MessageKey.PARTICIPANTS_GROUPS) + ' • ' + group.title)
              for group in view.participant_groups),
        ]

    def open(self):
        self.identity = SessionIdentity.current(self.view).to_tuple()
        self.value = self.view.recipient_id
        super().open()

    def close(self):
        self.expanded = self.CLOSED
        get_app().layout.focus(self.view.composer)
        get_app().invalidate()

    def select(self, identifier):
        if self.identity != SessionIdentity.current(self.view).to_tuple():
            return
        if identifier not in {key for key, _ in self.recipients()}:
            return
        self.view.select_recipient(identifier)
