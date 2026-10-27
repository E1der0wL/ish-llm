"""Transient search popup; conversation output never receives keyboard focus."""

from hub.locales.keys import MessageKey

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label
from ..dialog import Dialog, PopupSize
from ..controls import Button, TextArea
from ..focus import focus_control
from ...ui.input.registry import ShortcutRegistry
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel


class SessionSearch:
    ABSENT = None
    EMPTY_QUERY = ""
    NEXT = 1
    PREVIOUS = -1
    COUNT_ORIGIN = 0
    KEEP_INPUT = True
    MULTILINE = False
    INPUT_HEIGHT = 1
    PREFERRED_WIDTH = 58

    def __init__(self, view, transcript):
        self.view, self.transcript = view, transcript
        self.dialog = SessionSearch.ABSENT
        self.input = SessionSearch.ABSENT
        self.shortcuts = ShortcutRegistry()
        self.shortcuts.add(
            [KeyName.ENTER],
            ShortcutLabel.ENTER,
            ShortcutAction.NEXT_MATCH,
            lambda e: self.move(),
            when=lambda: view.visible
            and self.active
            and get_app().layout.current_control is self.input.control,
        )
        self.shortcuts.add(
            [(KeyName.ESCAPE, KeyName.P)],
            ShortcutLabel.ALT_P,
            ShortcutAction.PREVIOUS_MATCH,
            lambda e: self.move(SessionSearch.PREVIOUS),
            when=lambda: view.visible and self.active,
        )

    @property
    def active(self):
        return (
            self.dialog is not SessionSearch.ABSENT and self.view._dialog is self.dialog
        )

    def count(self):
        control = self.transcript.control
        if not control.search_query:
            return self.view.t(MessageKey.SEARCH_HINT)
        return self.view.t(
            MessageKey.SEARCH_COUNT,
            index=max(SessionSearch.COUNT_ORIGIN, control.search_index + 1),
            count=len(control.search_hits),
        )

    def move(self, direction=NEXT):
        self.transcript.control.move_search(direction)
        get_app().invalidate()

    def open(self):
        view, app = self.view, get_app()
        if self.active:
            focus_control(self.input)
            return
        view.composer.buffer.cancel_completion()
        self.input = TextArea(
            text=self.transcript.control.search_query,
            multiline=SessionSearch.MULTILINE,
            height=SessionSearch.INPUT_HEIGHT,
            width=Dimension(preferred=SessionSearch.PREFERRED_WIDTH),
        )

        def changed(_):
            self.transcript.control.set_search(self.input.text)
            app.invalidate()

        self.input.buffer.on_text_changed += changed

        def next_match(_):
            self.move()
            return SessionSearch.KEEP_INPUT

        self.input.buffer.accept_handler = next_match
        self.dialog = Dialog(
            title=view.t(MessageKey.SEARCH_TITLE),
            size=PopupSize.CUSTOM,
            body=HSplit([self.input, Label(self.count)]),
            buttons=[
                Button(
                    view.t(MessageKey.SEARCH_PREVIOUS),
                    handler=lambda: self.move(SessionSearch.PREVIOUS),
                ),
                Button(view.t(MessageKey.SEARCH_NEXT), handler=self.move),
                Button(view.t(MessageKey.SEARCH_CLOSE), handler=view.close_dialog),
            ],

            with_background=Dialog.DEFAULT_BACKGROUND,
            modal=Dialog.DEFAULT_MODAL,
        )
        self.dialog.dispose = lambda: self.transcript.control.set_search(
            SessionSearch.EMPTY_QUERY
        )
        view.dialogs.show(self.dialog, self.input)
        app.invalidate()
