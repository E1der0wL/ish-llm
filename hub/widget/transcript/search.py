"""Transient search popup; conversation output never receives keyboard focus."""

from hub.locales.keys import MessageKey

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label
from ..dialog import Dialog
from ..controls import Button, TextArea
from ..focus import focus_control
from ...ui.input.registry import ShortcutRegistry


class SessionSearch:
    def __init__(self, view, transcript):
        self.view, self.transcript = view, transcript
        self.dialog = None
        self.input = None
        self.shortcuts = ShortcutRegistry()
        self.shortcuts.add(["enter"], "Enter", "next_match", lambda e: self.move(),
            when=lambda: view.visible and self.active and get_app().layout.current_control is self.input.control)
        self.shortcuts.add([("escape", "p")], "Alt+P", "previous_match", lambda e: self.move(-1),
            when=lambda: view.visible and self.active)

    @property
    def active(self):
        return self.dialog is not None and self.view._dialog is self.dialog

    def count(self):
        control = self.transcript.control
        if not control.search_query:
            return self.view.t(MessageKey.SEARCH_HINT)
        return self.view.t(MessageKey.SEARCH_COUNT, index=max(0, control.search_index + 1), count=len(control.search_hits))

    def move(self, direction=1):
        self.transcript.control.move_search(direction)
        get_app().invalidate()

    def open(self):
        view, app = self.view, get_app()
        if self.active:
            focus_control(self.input)
            return
        view.composer.buffer.cancel_completion()
        self.input = TextArea(text=self.transcript.control.search_query, multiline=False, height=1)
        def changed(_):
            self.transcript.control.set_search(self.input.text)
            app.invalidate()
        self.input.buffer.on_text_changed += changed
        def next_match(_):
            self.move()
            return True
        self.input.buffer.accept_handler = next_match
        self.dialog = Dialog(title=view.t(MessageKey.SEARCH_TITLE),
            body=HSplit([self.input, Label(self.count)]),
            buttons=[Button(view.t(MessageKey.SEARCH_PREVIOUS), handler=lambda: self.move(-1)),
                     Button(view.t(MessageKey.SEARCH_NEXT), handler=self.move),
                     Button(view.t(MessageKey.SEARCH_CLOSE), handler=view.close_dialog)],
            width=Dimension(preferred=58, max=70), with_background=False, modal=False)
        self.dialog.dispose = lambda: self.transcript.control.set_search("")
        view.dialogs.show(self.dialog, self.input)
        app.invalidate()
