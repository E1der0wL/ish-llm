"""Review exact or command-range conditions before granting remembered approval."""

import json

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.widgets import Label

from ...backend.interactions.command_range import CommandChain, CommandRangeApproval
from ...backend.interactions.remembered import ApprovalMatch, ApprovalSelection, RememberScope, ToolApprovalConditions
from ...config.theme_style import ThemeSelector
from ...locales.keys import MessageKey
from ..controls import Button, RadioList, TextArea
from ..dialog import Dialog, PopupSize
from ..section import Heading


class ApprovalSelectionDialog(Dialog):
    def __init__(self, question, match, option_id, value):
        self.question, self.match = question, match
        self.option_id, self.value = option_id, value
        self.identity, self.item = question.identity, question.item
        view, t = question.view, question.view.t
        self.status = ''
        request = question.observation.request.to_record()
        self.action = {key: request['action'][key] for key in ToolApprovalConditions.ACTION_FIELDS}
        ranged = match == ApprovalMatch.COMMAND_RANGE
        body = [Heading(t(MessageKey.APPROVAL_CURRENT_ACTION))]
        self.original = TextArea(text=json.dumps(self.action['arguments'], ensure_ascii=False, indent=2),
            read_only=True, focusable=False, wrap_lines=True, height=5,
            style=ThemeSelector.POPUP_LIST.style)
        body.extend((self.original, Window(height=1)))
        if ranged:
            chain = CommandChain.parse(self.action['arguments'][CommandRangeApproval.COMMAND])
            self.prefixes = TextArea(text=CommandChain.text(chain.suggested()), multiline=True,
                wrap_lines=False, height=max(2, min(5, len(chain.commands))),
                style=ThemeSelector.POPUP_LIST.style)
            body.extend((Heading(t(MessageKey.APPROVAL_PREFIXES)), self.prefixes,
                Label(t(MessageKey.APPROVAL_PREFIXES_HELP), style=ThemeSelector.MUTED.style), Window(height=1)))
        else:
            self.prefixes = None
        body.extend((Label(t(MessageKey.APPROVAL_RANGE_NOTICE if ranged else MessageKey.APPROVAL_EXACT_NOTICE),
                           style=ThemeSelector.MUTED.style), Window(height=1),
                     Heading(t(MessageKey.APPROVAL_LIFETIME))))
        self.scope = RadioList([(RememberScope.SESSION, t(MessageKey.APPROVAL_SESSION)),
                                (RememberScope.PROJECT, t(MessageKey.APPROVAL_PROJECT))],
                               default=RememberScope.SESSION, select_on_focus=True)
        body.append(self.scope)
        super().__init__(title=t(MessageKey.APPROVAL_RANGE if ranged else MessageKey.APPROVAL_EXACT),
            body=HSplit(body), size=PopupSize.MEDIUM,
            buttons=[Button(t(MessageKey.APPROVAL_ALLOW), handler=self.confirm),
                     Button(t(MessageKey.CANCEL), handler=view.close_dialog)],
            status_bar=view.progress.container(lambda: self.status, surface=self))

    def open(self):
        self.question.view.dialogs.show(self, self.prefixes or self.scope)

    def confirm(self):
        question, view = self.question, self.question.view
        if question.identity != self.identity or question.busy:
            self.status = view.t(MessageKey.APPROVAL_REQUEST_CHANGED)
            return
        try:
            prefixes = CommandChain.prefixes(self.prefixes.text) if self.prefixes else ()
            if self.prefixes:
                CommandRangeApproval.condition(self.action, prefixes)
        except (ValueError, TypeError):
            self.status = view.t(MessageKey.APPROVAL_INVALID_RANGE)
            return
        selection = ApprovalSelection(RememberScope(self.scope.current_value), self.match, prefixes)
        view.dialogs.close(accepted=True)
        view.on_answer(self.item, self.option_id, self.value, selection)
