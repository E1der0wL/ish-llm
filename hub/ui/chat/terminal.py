"""Explicit observation target, character page and review request selection."""

import json
from prompt_toolkit.layout import HSplit, VSplit
from hub.locales.keys import MessageKey
from hub.widget.controls import Button, RadioList, TextArea
from hub.backend.terminal_values import TerminalCatalog, TerminalStep, TerminalKind, TerminalOffsets, TerminalPage, TerminalOperation
from hub.ui.input.json_document import JsonDocument


class TerminalReview:
    PROMPT_SEPARATOR = ': '
    PROCESS_LABEL = '{identifier} · {status} · {source}'
    STEP_LABEL = '{name} · {status} · {identifier}'
    RUN_LABEL = 'Run {identifier}'
    PATH_SEPARATOR = ' / '
    PARAGRAPH = '\n\n'
    NEWLINE = '\n'
    def __init__(self, controller):
        self.controller, self.view = controller, controller.view
        self.scope = (self.view.project_id, self.view.sessions[self.view.selected].id)
        self.target, self.last_page, self.popup = None, None, None
        self.offsets = TerminalOffsets()
        self.evidence = []
        self.limit = TextArea(text='', multiline=False,
                              prompt=self.view.t(MessageKey.TERMINAL_PAGE_SIZE) + self.PROMPT_SEPARATOR)
        self.body = TextArea(text='', multiline=True, read_only=True)
        self.question = TextArea(text='', multiline=True,
                                 prompt=self.view.t(MessageKey.TERMINAL_QUESTION) + self.PROMPT_SEPARATOR)

    def current(self):
        return (self.view.visible and self.view._dialog is self.popup
                and self.scope == (self.view.project_id, self.view.sessions[self.view.selected].id))

    def open(self):
        actions = VSplit([Button(self.view.t(label), handler=action) for label, action in (
            (MessageKey.TERMINAL_SELECT, self.select), (MessageKey.TERMINAL_READ, self.read),
            (MessageKey.TERMINAL_NEXT, self.next), (MessageKey.TERMINAL_CAPTURE, self.capture),
            (MessageKey.TERMINAL_REVIEW, self.review))], padding=1)
        self.view.open_dialog(self.view.t(MessageKey.TERMINAL_TITLE),
            HSplit([actions, self.limit, self.body, self.question]), lambda: None, self.limit)
        self.popup = self.view._dialog

    def select(self):
        def ready(value):
            if value is None or not self.current():
                return
            value = TerminalCatalog.from_dict(value)
            if value.process_error is not None:
                self.view.notice = value.process_error
            choices = [((TerminalKind.PROCESS, identifier), self.PROCESS_LABEL.format(identifier=identifier, status=item.record.status, source=item.source))
                       for identifier, item in value.processes.items()]
            choices += [((TerminalKind.RUN, identifier), self.RUN_LABEL.format(identifier=identifier)) for identifier in value.runs]
            if not choices:
                self.view.notice = self.view.t(MessageKey.TERMINAL_EMPTY)
                return
            self._choose(choices, self._selected)
        self.controller._call(TerminalOperation.CATALOG, self.scope[1], completed=ready)

    def _choose(self, choices, selected):
        selection = RadioList(choices)
        parent = self.popup
        def restore(): self.view.dialogs.show(parent, self.limit)
        def accept():
            value = selection.current_value
            restore()
            selected(value)
        self.view.open_dialog(self.view.t(MessageKey.TERMINAL_SELECT), selection, accept,
                              selection, on_cancel=restore)

    def _selected(self, target):
        if target[0] == TerminalKind.RUN:
            def ready(values):
                if values is None or not self.current(): return
                if not values:
                    self.view.notice = self.view.t(MessageKey.TERMINAL_EMPTY)
                    return
                rows = [TerminalStep.from_dict(value) for value in values]
                self._choose([((TerminalKind.STEP, target[1], row.id),
                               self.STEP_LABEL.format(name=row.name, status=row.status, identifier=row.id)) for row in rows], self._selected)
            self.controller._call(TerminalOperation.STEPS, self.scope[1], target[1], completed=ready)
            return
        self.target, self.last_page = list(target), None
        self.offsets = TerminalOffsets()
        self.body.text = self.PATH_SEPARATOR.join(target)

    def read(self):
        if not self.current():
            return
        try:
            limit = int(self.limit.text)
            if limit <= 0 or self.target is None:
                raise ValueError(self.view.t(MessageKey.TERMINAL_SELECT_LIMIT))
        except ValueError:
            self.view.notice = self.view.t(MessageKey.TERMINAL_SELECT_LIMIT)
            return
        target, offsets = list(self.target), self.offsets
        def ready(value):
            if value is None or not self.current() or target != self.target or offsets != self.offsets:
                return
            self.last_page = TerminalPage.from_dict(value)
            self.body.text = JsonDocument.render(value, allow_non_finite=JsonDocument.ALLOW_NON_FINITE_PREVIEW)
        self.controller._call(TerminalOperation.PAGE, self.scope[1], target, offsets.to_dict(), limit, completed=ready)

    def next(self):
        if self.last_page is None:
            return self.read()
        self.offsets = self.offsets.after(self.last_page.page)
        self.read()

    def capture(self):
        if self.current() and self.last_page is not None and self.last_page not in self.evidence:
            self.evidence.append(self.last_page)
            self.view.notice = self.view.t(MessageKey.TERMINAL_CAPTURED, count=len(self.evidence))

    def review(self):
        if not self.current():
            return
        if not self.evidence or not self.question.text.strip():
            self.view.notice = self.view.t(MessageKey.TERMINAL_SELECT_EVIDENCE)
            return
        text = (self.question.text + self.PARAGRAPH + self.view.t(MessageKey.TERMINAL_REVIEW_BOUNDARY)
                + self.NEWLINE + JsonDocument.render([page.to_dict() for page in self.evidence], allow_non_finite=JsonDocument.ALLOW_NON_FINITE_PREVIEW))
        self.view.close_dialog()
        self.controller.submit(self.scope[1], text)
