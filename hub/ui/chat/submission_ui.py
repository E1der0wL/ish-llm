"""Explicit queue/steering choice before sending input during active work."""

from prompt_toolkit.application.current import get_app

from ...backend.runtime import HubRuntime
from ...locales.keys import MessageKey
from ...widget.controls import RadioList, Button
from ...widget.dialog import Dialog, PopupSize
from ...widget.focus import focus_control
from ..input.registry import ShortcutRegistry
from ..input.keys import KeyName, ShortcutAction, ShortcutLabel


class RunningSubmission:
    STEER = 'steer'
    FOLLOW_UP = 'follow_up'

    def __init__(self, controller, session_id, text, recipients, options, draft):
        self.controller, self.view = controller, controller.view
        self.session_id, self.text = session_id, text
        self.recipients, self.options, self.draft = recipients, options, draft
        self.busy = self.selecting_run = False
        self.choices = self._choices([
            (self.STEER, self.view.t(MessageKey.SEND_STEERING)),
            (self.FOLLOW_UP, self.view.t(MessageKey.SEND_FOLLOW_UP)),
        ], default=self.FOLLOW_UP)
        self.popup = Dialog(
            title=self.view.t(MessageKey.SEND_WHILE_RUNNING), size=PopupSize.CUSTOM,
            body=self.choices,
            buttons=[Button(self.view.t(MessageKey.CONFIRM), handler=self.accept),
                     Button(self.view.t(MessageKey.SEARCH_CLOSE), handler=self.view.close_dialog)],
        )
        self.popup.on_cancel = lambda: self.draft.restore(self.view)

    def _choices(self, values, **kwargs):
        choices = RadioList(values, select_on_focus=True, **kwargs)
        ShortcutRegistry.require(choices.control).add(
            [KeyName.ENTER], ShortcutLabel.ENTER, ShortcutAction.CONFIRM,
            lambda e: self.accept())
        return choices

    def open(self):
        self.view.dialogs.show(self.popup, self.choices)

    def _steer(self, run_id):
        self.view.dialogs.close(accepted=True)
        self.controller.steer(self.session_id, self.text, expected_run_id=run_id, draft=self.draft)

    def accept(self):
        if self.busy or self.view._dialog is not self.popup:
            return
        if not self.draft.is_current(self.view):
            self.view.close_dialog()
            return
        if self.selecting_run:
            self._steer(self.choices.current_value)
        elif self.choices.current_value == self.FOLLOW_UP:
            self.view.dialogs.close(accepted=True)
            self.controller._send(HubRuntime.submit_input.__name__, self.session_id, self.text,
                                  self.text, self.recipients, self.options, False, draft=self.draft)
        else:
            self.busy = True
            self.controller._call(HubRuntime.running_recipients.__name__, self.session_id,
                                  self.recipients, completed=self._runs_ready)

    def _runs_ready(self, runs):
        self.busy = False
        if self.view._dialog is not self.popup or not self.draft.is_current(self.view):
            return
        if runs is None:
            return
        if not runs:
            self.view.notice = self.view.t(MessageKey.NO_ACTIVE_RUN)
        elif len(runs) == 1:
            self._steer(runs[0][0])
        else:
            self.selecting_run = True
            self.choices = self._choices([(run_id, self.view.agent_titles.get(agent_id, agent_id))
                                          for run_id, agent_id in runs])
            self.popup.title = self.view.t(MessageKey.CHOOSE_TARGET)
            self.popup.body = self.choices
            get_app().layout.update_parents_relations()
            focus_control(self.choices)
        get_app().invalidate()
