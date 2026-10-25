"""Mouse actions share the composer's keyboard submission and stop handlers."""
from hub.ui.input.registry import ShortcutRegistry


from llm.core.models import RunStatus
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import VSplit

from .actions import bind_button_navigation
from .auxiliary import AuxiliaryButton
from ..asset.icon import AuxiliaryIcon, IconSet
from ..config.theme_style import ThemeSelector
from ..model import HubSnapshot, SessionIdentity
from ..ui.input.keys import KeyName, ShortcutAction, ShortcutLabel


class ComposerActions(VSplit):
    NO_EVENT = None
    NO_RUN = None
    GAP = 1
    HEIGHT = 1
    WIDTH = 7

    def __init__(self, view):
        self.view = view
        self.stop = AuxiliaryButton(self.stop_label, handler=self.interrupt)
        self.send = AuxiliaryButton(
            lambda: IconSet.action(view.icons, AuxiliaryIcon.SEND.value),
            handler=lambda: view.bindings.submit(self.NO_EVENT),
        )
        self.buttons = (self.stop, self.send)
        bind_button_navigation(self.buttons)
        for button in self.buttons:
            ShortcutRegistry.require(button.control).add(
                [KeyName.ESCAPE],
                ShortcutLabel.ESCAPE,
                ShortcutAction.RETURN,
                lambda e: view._focus_composer(),
            )
        super().__init__(
            list(self.buttons),
            padding=self.GAP,
            height=self.HEIGHT,
            width=self.WIDTH,
            style=ThemeSelector.AUXILIARY.style,
        )

    def run(self):
        snapshot = HubSnapshot.last_observed(self.view)
        return (
            snapshot.run
            if snapshot
            and (snapshot.project_id, snapshot.selected_id)
            == SessionIdentity.current(self.view).to_tuple()
            else self.NO_RUN
        )

    def stop_label(self):
        run = self.run()
        key = SessionIdentity.current(self.view).to_tuple()
        if key in self.view.preparing.pending or key in self.view.interrupting.pending:
            return IconSet.action(self.view.icons, AuxiliaryIcon.STOP.value)
        snapshot = HubSnapshot.last_observed(self.view)
        return IconSet.action(
            self.view.icons,
            (
                AuxiliaryIcon.RESUME.value
                if run
                and not run.active
                and not snapshot.active_run_ids
                and run.status == RunStatus.INTERRUPTED
                and not snapshot.queued_count
                else AuxiliaryIcon.STOP.value
            ),
        )

    def interrupt(self):
        if self.view.on_interrupt:
            self.view.on_interrupt(self.view.sessions[self.view.selected].id)
        get_app().invalidate()
