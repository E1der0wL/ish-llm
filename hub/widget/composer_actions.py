"""Mouse actions share the composer's keyboard submission and stop handlers."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import VSplit

from .actions import bind_button_navigation
from .auxiliary import AuxiliaryButton


class ComposerActions(VSplit):
    def __init__(self, view):
        self.view = view
        self.stop = AuxiliaryButton(self.stop_label, handler=self.interrupt)
        self.send = AuxiliaryButton(lambda: view.icons.auxiliary["send"], handler=lambda: view.bindings.submit(None))
        self.buttons = (self.stop, self.send)
        bind_button_navigation(self.buttons)
        for button in self.buttons:
            button.control.hub_shortcuts.add(["escape"], "ESC", "return", lambda e: view._focus_composer())
        super().__init__(list(self.buttons), padding=1, height=1, width=7, style="class:hub.auxiliary")

    def run(self):
        snapshot = getattr(self.view, "_last_snapshot", None)
        return snapshot.run if snapshot and (snapshot.project_id, snapshot.selected_id) == (
            getattr(self.view, "project_id", ""), self.view.sessions[self.view.selected].id) else None

    def stop_label(self):
        run = self.run()
        key = getattr(self.view, "project_id", ""), self.view.sessions[self.view.selected].id
        if key in self.view.preparing.pending or key in self.view.interrupting.pending:
            return self.view.icons.auxiliary["stop"]
        snapshot = getattr(self.view, "_last_snapshot", None)
        return self.view.icons.auxiliary["resume" if run and not run.active and run.status == "interrupted"
                                         and not snapshot.queued_count else "stop"]

    def interrupt(self):
        if self.view.on_interrupt:
            self.view.on_interrupt(self.view.sessions[self.view.selected].id)
        get_app().invalidate()
