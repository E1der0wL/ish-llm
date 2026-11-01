"""Explicit observation target, character page and review request selection."""

import json
from prompt_toolkit.layout import HSplit, VSplit
from hub.locales.keys import MessageKey
from hub.widget.controls import Button, RadioList, TextArea


class TerminalReview:
    def __init__(self, controller):
        self.controller, self.view = controller, controller.view
        self.scope = (self.view.project_id, self.view.sessions[self.view.selected].id)
        self.target, self.last_page, self.popup = None, None, None
        self.offsets = {'text': 0, 'stdout': 0, 'stderr': 0}
        self.evidence = []
        self.limit = TextArea(text='', multiline=False,
                              prompt=self.view.t(MessageKey.TERMINAL_PAGE_SIZE) + ': ')
        self.body = TextArea(text='', multiline=True, read_only=True)
        self.question = TextArea(text='', multiline=True,
                                 prompt=self.view.t(MessageKey.TERMINAL_QUESTION) + ': ')

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
            if value['process_error'] is not None:
                self.view.notice = value['process_error']
            choices = [(('process', identifier), f"{identifier} · {item['record']['status']} · {item['source']}")
                       for identifier, item in value['processes'].items()]
            choices += [(('run', identifier), f'Run {identifier}') for identifier in value['runs']]
            if not choices:
                self.view.notice = self.view.t(MessageKey.TERMINAL_EMPTY)
                return
            self._choose(choices, self._selected)
        self.controller._call('terminal_catalog', self.scope[1], completed=ready)

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
        if target[0] == 'run':
            def ready(values):
                if values is None or not self.current(): return
                if not values:
                    self.view.notice = self.view.t(MessageKey.TERMINAL_EMPTY)
                    return
                self._choose([(('step', target[1], value['id']),
                               f"{value['name']} · {value['status']} · {value['id']}")
                              for value in values], self._selected)
            self.controller._call('terminal_steps', self.scope[1], target[1], completed=ready)
            return
        self.target, self.last_page = list(target), None
        self.offsets = {'text': 0, 'stdout': 0, 'stderr': 0}
        self.body.text = ' / '.join(target)

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
        target, offsets = list(self.target), dict(self.offsets)
        def ready(value):
            if value is None or not self.current() or target != self.target or offsets != self.offsets:
                return
            self.last_page = value
            self.body.text = json.dumps(value, ensure_ascii=False, indent=2)
        self.controller._call('terminal_page', self.scope[1], target, offsets, limit, completed=ready)

    def next(self):
        if self.last_page is None:
            return self.read()
        page = self.last_page['page']
        if self.target[0] == 'process':
            self.offsets.update(stdout=page['next_stdout_offset'], stderr=page['next_stderr_offset'])
        else:
            self.offsets['text'] = page['next_offset']
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
        text = (self.question.text + '\n\n' + self.view.t(MessageKey.TERMINAL_REVIEW_BOUNDARY)
                + '\n' + json.dumps(self.evidence, ensure_ascii=False, indent=2))
        self.view.close_dialog()
        self.controller.submit(self.scope[1], text)
