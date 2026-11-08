"""Nonblocking questions share the composer, never a second question dialog."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth

from llm.core.interactions import InteractionRequest, InteractionResponse, InteractionStatus
from ...config.theme_style import ThemeSelector
from ...asset.icon import AuxiliaryIcon, IconSet
from ...locales.keys import MessageKey
from ...model import QuestionPresentation, QuestionSource, SessionIdentity
from ...backend.interactions.question_operations import (QuestionAction, QuestionList, QuestionRun,
    QuestionAnswer, QuestionDelivery, QuestionTargets)
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ...ui.input.registry import ShortcutRegistry
from ..actions import ActionBar, ButtonNavigation, bind_button_navigation
from ..controls import Button
from ..dropdown import DropdownBox
from ..feedback.progress import ProgressCompletion


class QuestionInbox:
    """UI selection and drafts only; native APIs own answers and deliveries."""
    POSITION = ' {index}/{total} '
    PAGE_STYLE = ' nobold fg:'
    NEWLINE = '\n'
    PAGE_BUTTON_WIDTH = 3
    DESTINATION_WIDTH = 32
    BUTTON_PADDING = 2

    CLOSED_STATES = (InteractionStatus.CANCELLED, InteractionStatus.EXPIRED)

    def __init__(self, composer):
        self.composer, self.view = composer, composer.view
        self.identity = None
        self.items = {}
        self.blocking = []
        self.busy = False
        self.hidden = False
        self.status = ''
        self.delivery = None
        self.finished = set()
        self.targets = []
        self.open_button = self.button(MessageKey.QUESTION_ANSWER, self.open)
        self.previous_button = Button(lambda: IconSet.action(self.view.icons, AuxiliaryIcon.PREVIOUS),
                                      handler=lambda: self.move(-1), width=QuestionInbox.PAGE_BUTTON_WIDTH, focusable=False)
        self.next_button = Button(lambda: IconSet.action(self.view.icons, AuxiliaryIcon.NEXT),
                                  handler=lambda: self.move(1), width=QuestionInbox.PAGE_BUTTON_WIDTH, focusable=False)
        self.navigation_control = FormattedTextControl(self.position, focusable=True, show_cursor=False)
        self.bind_navigation(self.navigation_control)
        self.navigation = VSplit([
            self.previous_button,
            Window(self.navigation_control, width=lambda: get_cwidth(self.position()),
                   style=self.navigation_style),
            self.next_button,
        ], height=1, style=ThemeSelector.QUESTION_HEADING.style)
        self.destination = DropdownBox(lambda: [(i, row.label) for i, row in enumerate(self.targets)],
                                       width=QuestionInbox.DESTINATION_WIDTH, icons=lambda: self.view.icons)
        self.buttons = [
            self.button(MessageKey.QUESTION_SEND, composer.answer),
            self.button(MessageKey.QUESTION_BACK, composer.return_to_input),
        ]
        self.send_button = self.button(MessageKey.QUESTION_SEND, self.deliver)
        self.cancel_button = self.button(MessageKey.CANCEL, self.cancel_delivery)
        self.bind(self.destination.button)
        bind_button_navigation([self.send_button, self.cancel_button])
        self.container = ConditionalContainer(VSplit([
            Window(FormattedTextControl(lambda: self.view.t(MessageKey.QUESTION_PENDING_NOTICE,
                   count=len(self.outstanding()))), height=1, style=ThemeSelector.MUTED.style),
            self.open_button,
        ], height=1), Condition(self.can_open_current))
        self.actions = ConditionalContainer(HSplit([
            ConditionalContainer(ActionBar(self.buttons, self.width), Condition(lambda: self.delivery is None)),
            ConditionalContainer(HSplit([
                Window(FormattedTextControl(self.destination_label), height=1, style=ThemeSelector.MUTED.style),
                VSplit([self.destination, Window(width=1), self.send_button,
                        Window(width=1), self.cancel_button, Window()], height=1),
            ]), Condition(lambda: self.delivery is not None)),
            ConditionalContainer(Window(FormattedTextControl(lambda: self.status), wrap_lines=True,
                dont_extend_height=True, style=ThemeSelector.MUTED.style), Condition(lambda: bool(self.status))),
        ]), Condition(lambda: bool(composer.item and not composer.is_approval)))

    def width(self):
        info = self.view.composer.window.render_info
        return info.window_width if info else get_app().output.get_size().columns

    @property
    def focused(self):
        controls = [self.open_button.control,
                    self.send_button.control, self.cancel_button.control,
                    self.navigation_control, *self.destination.controls,
                    *(button.control for button in self.buttons)]
        return get_app().layout.current_control in controls

    def bind(self, button):
        keys = ShortcutRegistry.require(button.control)
        keys.add([KeyName.ESCAPE], ShortcutLabel.ESCAPE, ShortcutAction.RETURN,
                 lambda e: e.app.layout.focus(self.view.composer))
        keys.add([KeyName.TAB, KeyName.SHIFT_TAB], ShortcutLabel.TAB_SHIFT_TAB, ShortcutAction.MOVE,
                 lambda e: self.focus_next(e.key_sequence[0].key == KeyName.SHIFT_TAB))
        return button

    def button(self, key, handler):
        text = self.view.t(key)
        return self.bind(Button(text, width=get_cwidth(text) + QuestionInbox.BUTTON_PADDING, handler=handler))

    def navigation_style(self):
        focused = get_app().layout.current_control is self.navigation_control
        foreground = self.view.theme.blue if focused else self.view.theme.muted
        return ThemeSelector.QUESTION_HEADING.style + self.PAGE_STYLE + foreground

    def bind_navigation(self, control):
        # The region owns keyboard focus; its two icons are mouse-only buttons.
        # Confirming the region returns to typing without submitting an answer.
        keys = ShortcutRegistry().attach(control)
        control.key_bindings = keys.bindings
        keys.add([KeyName.LEFT, KeyName.RIGHT], ShortcutLabel.HORIZONTAL, ShortcutAction.SELECT,
                 lambda e: self.navigate(e.key_sequence[-1].key == KeyName.LEFT))
        keys.add([KeyName.TAB, KeyName.SHIFT_TAB], ShortcutLabel.TAB_SHIFT_TAB, ShortcutAction.RETURN,
                 lambda e: e.app.layout.focus(self.view.composer))
        keys.add([KeyName.SPACE, KeyName.ENTER], ShortcutLabel.ENTER_SPACE, ShortcutAction.RETURN,
                 lambda e: e.app.layout.focus(self.view.composer))
        keys.add([KeyName.ESCAPE], ShortcutLabel.ESCAPE, ShortcutAction.RETURN,
                 lambda e: e.app.layout.focus(self.view.composer))

    def navigate(self, previous):
        self.move(-1 if previous else 1)
        get_app().layout.focus(self.navigation_control)

    def focus_next(self, reverse=False):
        current = get_app().layout.current_control
        if self.delivery is None or current is self.view.composer.control:
            target = (self.navigation_control if current is self.view.composer.control
                      else self.view.composer)
            get_app().layout.focus(target)
            return
        controls = [self.destination.control, self.send_button.control,
                    self.cancel_button.control, self.view.composer.control]
        groups = ButtonNavigation.tab_groups(controls)
        index = next((i for i, group in enumerate(groups) if current in group), -1)
        get_app().layout.focus(groups[(index + (-1 if reverse else 1)) % len(groups)][0])

    def outstanding(self):
        return [*self.blocking, *(item for item in self.items.values()
                                 if not QuestionPresentation.observe(item).delivery)]

    def can_open_current(self):
        return (not self.view.no_sessions and self.composer.item is None
                and self.identity == SessionIdentity.current(self.view).to_tuple()
                and bool(self.outstanding()))

    def position(self):
        items = self.outstanding()
        index = next((i for i, item in enumerate(items, 1)
                      if self.composer.item and QuestionPresentation.identity(*self.identity, item)
                      == self.composer.identity), 0)
        return self.POSITION.format(index=index, total=len(items))

    def move(self, direction):
        items = self.outstanding()
        if not items or self.composer.is_approval:
            return
        index = next((i for i, item in enumerate(items)
                      if QuestionPresentation.identity(*self.identity, item) == self.composer.identity), 0)
        target = max(0, min(len(items) - 1, index + direction))
        if target != index:
            focused = get_app().layout.current_control
            self.select(QuestionPresentation.observe(items[target]).request.id)
            if focused is self.navigation_control:
                get_app().layout.focus(focused)

    def sync(self, project_id, session_id, questions):
        identity = (project_id, session_id)
        if self.identity != identity:
            self.identity, self.items = identity, {}
            self.hidden, self.delivery, self.status = False, None, ''
            self.finished.clear()
        self.blocking = [item for item in questions
            if not QuestionPresentation.observe(item).nonblocking
            and not QuestionPresentation.observe(item).source_finished
            and self.key(item) not in self.finished
            and QuestionPresentation.observe(item).request.kind != InteractionRequest.KIND_APPROVAL
            and QuestionPresentation.identity(*identity, item) not in self.composer.responses]
        for item in questions:
            observed = QuestionPresentation.observe(item)
            if observed.source_finished and observed.request.kind != InteractionRequest.KIND_APPROVAL:
                self.finish(item)
                continue
            if not observed.nonblocking:
                continue
            key = observed.request.id
            if self.key(item) in self.finished:
                continue
            if observed.status in self.CLOSED_STATES:
                self.items.pop(key, None)
                continue
            previous = self.items.get(key)
            if previous and QuestionPresentation.observe(previous).request.revision == observed.request.revision:
                # A queued observation may predate our successful native write.
                item = observed.retaining_answer(previous)
            self.items[key] = item
        if get_app().layout.current_control is self.open_button.control and not self.can_open_current():
            get_app().layout.focus(self.view.composer)

    @staticmethod
    def key(item):
        observed = QuestionPresentation.observe(item)
        return observed.run_id, observed.request.id, observed.request.revision

    def finish(self, item):
        """Close a finished source's editor, never cancel its durable question."""
        key = self.key(item)
        if key in self.finished:
            return
        self.finished.add(key)
        observed = QuestionPresentation.observe(item)
        previous = self.items.pop(observed.request.id, None)
        self.blocking = [entry for entry in self.blocking if self.key(entry) != key]
        identity = QuestionPresentation.identity(*self.identity, item)
        selected = self.composer.identity == identity
        if selected:
            self.delivery = None
            self.composer.leave()
        draft = self.composer.drafts.pop(identity, '')
        saved = observed.retaining_answer(previous)
        if QuestionPresentation.observe(saved).delivery:
            return
        if not draft.strip() and QuestionPresentation.observe(saved).response:
            draft = self.answer_text(saved)
        if draft.strip():
            choices = QuestionSource.suggestions(observed.request.source) if observed.text_answer else []
            if draft.isdecimal() and 1 <= int(draft) <= len(choices):
                draft = choices[int(draft) - 1]
            text = observed.request.title + self.NEWLINE + draft
            self.preserve_draft(text)
        if selected or draft.strip():
            self.view.notice = self.view.t(MessageKey.QUESTION_FINISHED)

    def preserve_draft(self, text):
        from ...ui.view import HubView
        # A different answer or normal message must never be overwritten.
        if not self.composer.item and not self.view.composer.text:
            self.composer.replace_input(text)
            HubView.store_composer_draft(self.view, text)
        else:
            self.view.input_history.recover(self.view._draft_key(self.view.selected), text)

    def candidate(self):
        return next(iter(self.outstanding()), None) if not self.hidden else None

    def selected(self):
        item = self.composer.item
        return item if item and QuestionPresentation.observe(item).nonblocking else None

    def select(self, key):
        if self.composer.is_approval:
            self.view.notice = self.view.t(MessageKey.QUESTION_BLOCKING_FIRST)
            return
        item = next((item for item in self.outstanding()
                     if QuestionPresentation.observe(item).request.id == key), None)
        if item:
            self.hidden, self.delivery, self.status = False, None, ''
            self.composer.select(item, *self.identity)
            get_app().layout.focus(self.view.composer)

    def call(self, operation, payload, done):
        if self.busy or not self.view.on_question_action or not self.identity or not all(self.identity):
            return
        self.busy = True
        identity = self.identity
        task = self.view.progress.start(self.view.t(MessageKey.PROGRESS_BACKEND_READ
            if operation in (QuestionAction.LIST, QuestionAction.TARGETS) else MessageKey.PROGRESS_BACKEND_WRITE))

        def received(result):
            self.view.progress.finish(task)
            self.busy = False
            if identity == self.identity:
                done(result)
            get_app().invalidate()

        try:
            self.view.on_question_action(*identity, operation, payload,
                ProgressCompletion(self.view.progress, task, received))
        except Exception:
            self.view.progress.finish(task)
            self.busy = False
            raise

    def open(self):
        if not self.can_open_current():
            return
        self.hidden = False
        if item := self.candidate():
            self.select(QuestionPresentation.observe(item).request.id)

        def loaded(items):
            if self.identity != SessionIdentity.current(self.view).to_tuple():
                return  # Selection can change before the next backend snapshot.
            if items is not None:
                self.items = {}
                self.sync(*self.identity, [*self.blocking, *items])
                if not self.hidden and not self.composer.item:
                    if item := self.candidate():
                        self.select(QuestionPresentation.observe(item).request.id)
                    else:
                        self.view.notice = self.view.t(MessageKey.QUESTION_EMPTY)
        self.call(QuestionAction.LIST, QuestionList(tuple(dict.fromkeys(
            QuestionPresentation.observe(item).run_id for item in self.items.values()))).to_dict(), loaded)

    @staticmethod
    def answer_text(item):
        observed = QuestionPresentation.observe(item)
        if not observed.response:
            return ''
        request = InteractionRequest.from_dict(observed.request.to_record())
        return str(InteractionResponse.from_dict(observed.response).decision(request))

    def save(self, item, option_id, value, after=None):
        if self.busy or not self.view.on_question_action:
            return
        observed = QuestionPresentation.observe(item)
        if observed.response:
            if after:
                after(item)
            return
        self.status = self.view.t(MessageKey.QUESTION_SENDING, title=observed.request.title)
        draft = self.view.composer.text
        identity = self.composer.identity
        self.composer.drafts[identity] = draft
        self.composer.replace_input('')

        def saved(result):
            self.status = self.view.t(MessageKey.QUESTION_SAVED if result is not None else MessageKey.TOOLS_FAILED)
            if self.key(item) in self.finished:
                return  # A late receipt must not reopen or deliver to an ended task.
            if result is not None:
                updated = observed.with_response(result)
                self.items[observed.request.id] = updated
                if self.composer.identity == identity:
                    self.composer.item = updated
                    self.composer.replace_input(self.answer_text(updated))
                    if after:
                        after(updated)
            elif self.composer.identity == identity:
                self.composer.replace_input(draft)
            else:
                self.composer.drafts[identity] = draft

        self.call(QuestionAction.SAVE, QuestionAnswer(observed.run_id,
            InteractionRequest.from_dict(observed.request.to_record()), option_id, value).to_dict(), saved)

    def prepare_delivery(self):
        if self.busy or not (item := self.selected()):
            return
        observed = QuestionPresentation.observe(item)
        if observed.delivery:
            self.status = self.view.t(MessageKey.QUESTION_DELIVERED)
            return
        if not observed.response:
            self.composer.answer()
            return

        def loaded(result):
            if result is None:
                return  # A read error is not proof that the source has ended.
            if not self.same_selection(item):
                return
            result = QuestionTargets.from_dict(result)
            latest = observed.with_run_status(result.status)
            if QuestionPresentation.observe(latest).source_finished:
                self.finish(latest)
                return
            own = [row for row in result.targets if row.run_id == observed.run_id]
            if len(own) == 1:
                self.send_to(item, own[0])
            elif own:
                self.choose_target(item, own)
            else:
                self.status = self.view.t(MessageKey.QUESTION_NO_TARGET)
                get_app().layout.focus(self.view.composer)
        self.call(QuestionAction.TARGETS, QuestionRun(observed.run_id).to_dict(), loaded)

    def choose_target(self, item, rows):
        if not self.same_selection(item):
            return
        if not rows:
            self.status = self.view.t(MessageKey.QUESTION_NO_TARGET)
            return
        self.delivery, self.targets = item, rows
        self.destination.value = None
        self.destination.placeholder = self.view.t(MessageKey.QUESTION_SELECT_TARGET)
        self.status = ''
        get_app().layout.focus(self.destination)

    def same_selection(self, item):
        return bool(self.selected() and self.key(self.selected()) == self.key(item)
                    and self.key(item) not in self.finished)

    def destination_label(self):
        return self.view.t(MessageKey.QUESTION_TARGET)

    def cancel_delivery(self):
        self.delivery = None
        get_app().layout.focus(self.view.composer)

    def deliver(self):
        if self.busy or self.delivery is None or self.destination.value is None:
            return
        item = self.delivery
        choice = self.targets[self.destination.value]
        self.send_to(item, choice)

    def send_to(self, item, choice):
        if self.busy or not self.same_selection(item):
            return
        observed = QuestionPresentation.observe(item)
        payload = QuestionDelivery(observed.run_id, observed.request.id,
                                   choice.run_id, (choice.target_id,)).to_dict()

        def delivered(result):
            if self.key(item) in self.finished:
                return
            self.status = self.view.t(MessageKey.QUESTION_RECEIPT if result is not None else MessageKey.TOOLS_FAILED)
            if result is not None:
                self.items[observed.request.id] = observed.with_delivery(result)
                self.delivery = None
                self.view.notice = self.status
                if self.selected() and self.composer.observation.request.id == observed.request.id:
                    self.composer.return_to_input()
        self.call(QuestionAction.DELIVER, payload, delivered)
