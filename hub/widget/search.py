"""Transient search popup; conversation output never receives keyboard focus."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label
from .dialog import Dialog
from .controls import Button, TextArea


class SessionSearch:
    def __init__(self, view):
        self.view = view
        self.dialog = None
        self.input = None

    @property
    def active(self):
        return self.dialog is not None and self.view._dialog is self.dialog

    def count(self):
        control = self.view.transcript.control
        if not control.search_query:
            return self.view.t("search_hint")
        return self.view.t("search_count", index=max(0, control.search_index + 1), count=len(control.search_hits))

    def move(self, direction=1):
        self.view.transcript.control.move_search(direction)
        get_app().invalidate()

    def open(self):
        view, app = self.view, get_app()
        if self.active:
            app.layout.focus(self.input)
            return
        view.composer.buffer.cancel_completion()
        self.input = TextArea(text=view.transcript.control.search_query, multiline=False, height=1)
        def changed(_):
            view.transcript.control.set_search(self.input.text)
            app.invalidate()
        self.input.buffer.on_text_changed += changed
        def next_match(_):
            self.move()
            return True
        self.input.buffer.accept_handler = next_match
        self.dialog = Dialog(title=view.t("search_title"),
            body=HSplit([self.input, Label(self.count)]),
            buttons=[Button(view.t("search_previous"), handler=lambda: self.move(-1)),
                     Button(view.t("search_next"), handler=self.move),
                     Button(view.t("search_close"), handler=view.close_dialog)],
            width=Dimension(preferred=58, max=70), with_background=False, modal=False)
        view.dialogs.show(self.dialog, self.input)
        app.invalidate()
