"""Review exact or command-range conditions before granting remembered approval."""

import json

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.widgets import Label

from ...backend.interactions.command_range import CommandChain, CommandRangeApproval
from ...backend.interactions.approval_matching import MatchedToolAction
from ...backend.interactions.remembered import ApprovalMatch, ApprovalSelection, RememberScope, ToolApprovalConditions
from ...config.theme_style import ThemeSelector
from ...locales.keys import MessageKey
from ...ui.input.json_document import JsonDocument
from ..controls import Button, RadioList, TextArea
from ..dialog import Dialog, PopupSize
from ..section import Heading


class ApprovalSelectionDialog(Dialog):
    MINIMUM_PREFIX_ROWS = 2
    MAXIMUM_DETAIL_ROWS = 5
    def __init__(self, question, match, option_id, value):
        self.question = question
        self.option_id, self.value = option_id, value
        self.identity, self.item = question.identity, question.item
        view, t = question.view, question.view.t
        self.status = ''
        self.action = MatchedToolAction.capture(question.observation.request.action)
        matches = [(ApprovalMatch.EXACT, t(MessageKey.APPROVAL_EXACT))]
        if CommandRangeApproval.available(self.action):
            matches.append((ApprovalMatch.COMMAND_RANGE, t(MessageKey.APPROVAL_RANGE)))
        self.matching = RadioList(matches, default=match, select_on_focus=True)
        body = [Heading(t(MessageKey.APPROVAL_CURRENT_ACTION))]
        self.original = TextArea(text=JsonDocument.render(self.action.arguments, allow_non_finite=False),
            read_only=True, focusable=False, wrap_lines=True, height=ApprovalSelectionDialog.MAXIMUM_DETAIL_ROWS,
            style=ThemeSelector.POPUP_LIST.style)
        body.extend((self.original, Window(height=1)))
        body.extend((Heading(t(MessageKey.APPROVAL_MATCHING)), self.matching, Window(height=1)))
        if len(matches) > 1:
            chain = CommandChain.parse(self.action.arguments[CommandRangeApproval.COMMAND])
            self.prefixes = TextArea(text=CommandChain.text(chain.suggested()), multiline=True,
                wrap_lines=False, height=max(ApprovalSelectionDialog.MINIMUM_PREFIX_ROWS, min(ApprovalSelectionDialog.MAXIMUM_DETAIL_ROWS, len(chain.commands))),
                style=ThemeSelector.POPUP_LIST.style)
            body.append(ConditionalContainer(HSplit([
                Heading(t(MessageKey.APPROVAL_PREFIXES)), self.prefixes,
                Label(t(MessageKey.APPROVAL_PREFIXES_HELP), style=ThemeSelector.MUTED.style), Window(height=1)]),
                Condition(lambda: self.match == ApprovalMatch.COMMAND_RANGE)))
        else:
            self.prefixes = None
        body.extend((Label(lambda: t(MessageKey.APPROVAL_RANGE_NOTICE if self.match == ApprovalMatch.COMMAND_RANGE else MessageKey.APPROVAL_EXACT_NOTICE),
                           style=ThemeSelector.MUTED.style), Window(height=1),
                     Heading(t(MessageKey.APPROVAL_LIFETIME))))
        self.scope = RadioList([(RememberScope.SESSION, t(MessageKey.APPROVAL_SESSION)),
                                (RememberScope.PROJECT, t(MessageKey.APPROVAL_PROJECT))],
                               default=RememberScope.SESSION, select_on_focus=True)
        body.append(self.scope)
        super().__init__(title=t(MessageKey.APPROVAL_REMEMBER),
            body=HSplit(body), size=PopupSize.MEDIUM,
            buttons=[Button(t(MessageKey.APPROVAL_ALLOW), handler=self.confirm),
                     Button(t(MessageKey.CANCEL), handler=view.close_dialog)],
            status_bar=view.progress.container(lambda: self.status, surface=self))

    def open(self):
        self.question.view.dialogs.show(self, self.matching)

    @property
    def match(self):
        return ApprovalMatch(self.matching.current_value)

    def confirm(self):
        question, view = self.question, self.question.view
        if question.identity != self.identity or question.busy:
            self.status = view.t(MessageKey.APPROVAL_REQUEST_CHANGED)
            return
        try:
            prefixes = (CommandChain.prefixes(self.prefixes.text)
                        if self.match == ApprovalMatch.COMMAND_RANGE else ())
            if self.match == ApprovalMatch.COMMAND_RANGE:
                CommandRangeApproval.condition(self.action, prefixes)
        except (ValueError, TypeError):
            self.status = view.t(MessageKey.APPROVAL_INVALID_RANGE)
            return
        selection = ApprovalSelection(RememberScope(self.scope.current_value), self.match, prefixes)
        view.dialogs.close(accepted=True)
        view.on_answer(self.item, self.option_id, self.value, selection)
