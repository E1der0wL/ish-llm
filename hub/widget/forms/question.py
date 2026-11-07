"""Questions and approvals reuse the chat composer without another focus target."""

from hub.locales.keys import MessageKey

import json
from llm.core.interactions import InteractionOption, InteractionRequest
from ...backend.interactions.remembered import approval_choices
from ...model import HubSnapshot, QuestionSource, QuestionPresentation, SessionIdentity
from ...config.theme_style import ThemeSelector
from llm.core.schema import SchemaKeyword, SchemaType

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Window, HSplit, VSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from .question_card import QuestionCard
from ..input_contract import ControlFragment
from .answer_choices import AnswerChoices
from ..feedback.answer_feedback import AnswerFeedback, project_answers
from ..auxiliary import AuxiliaryButton
from ...asset.icon import AuxiliaryIcon, IconSet
from ...ui.input.registry import ShortcutRegistry
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from prompt_toolkit.application.current import get_app


class QuestionComposer:
    NO_ITEM = None
    NO_IDENTITY = None
    NO_DRAFT = None
    NO_RESPONSE = None
    NO_INPUT = None
    EMPTY_TEXT = ""
    NEWLINE = "\n"
    INDENT = " "
    HINT_SEPARATOR = "\n "
    MAX_RESPONSES = 128
    MINIMUM_HEIGHT = 1
    PREFERRED_HEIGHT = 3
    APPROVAL_DETAIL_ROWS = 4
    HEIGHT_DIVISOR = 3
    WRAP_LINES = True
    DONT_EXTEND = True
    CHOICE_DIGITS = tuple('123456789')

    def __init__(self, view):
        self.view = view
        self.item = self.NO_ITEM
        self.identity = self.NO_IDENTITY
        self.drafts = {}
        self.responses = {}
        self.approval_options = []
        from .question_inbox import QuestionInbox
        self.inbox = QuestionInbox(self)
        self.manage_button = AuxiliaryButton(
            lambda: IconSet.action(view.icons, AuxiliaryIcon.SETTINGS),
            handler=lambda: self.open_manager(),
        )
        ShortcutRegistry.require(self.manage_button.control).add(
            [KeyName.ESCAPE], ShortcutLabel.ESCAPE, ShortcutAction.RETURN,
            lambda e: e.app.layout.focus(view.composer))
        self.details = QuestionCard(self)
        self.choices = AnswerChoices(self)
        self.details.window.height = lambda: Dimension(
            min=self.MINIMUM_HEIGHT,
            max=self.APPROVAL_DETAIL_ROWS if self.is_approval else max(
                self.PREFERRED_HEIGHT, view._rows() // self.HEIGHT_DIVISOR),
        )
        form = ConditionalContainer(
            HSplit(
                [
                    VSplit([Window(
                        FormattedTextControl(self.heading),
                        wrap_lines=self.WRAP_LINES,
                        dont_extend_height=self.DONT_EXTEND,
                        style=ThemeSelector.QUESTION_HEADING.style,
                    ), ConditionalContainer(self.manage_button, Condition(
                        lambda: bool(self.item and self.observation.request.action)
                    )), ConditionalContainer(self.inbox.navigation, Condition(
                        lambda: bool(self.item and not self.is_approval)))], height=1),
                    ConditionalContainer(Window(
                        FormattedTextControl(self.tool_heading), dont_extend_height=True,
                        style=ThemeSelector.ACCENT.style),
                        Condition(lambda: bool(self.item and self.observation.request.action))),
                    self.details.window,
                    ConditionalContainer(
                        Window(
                            FormattedTextControl(self.risk_fragments),
                            wrap_lines=self.WRAP_LINES,
                            dont_extend_height=self.DONT_EXTEND,
                        ),
                        Condition(lambda: bool(self.item and self.observation.request.action)),
                    ),
                    self.choices,
                    Window(
                        FormattedTextControl(self.hint),
                        wrap_lines=self.WRAP_LINES,
                        dont_extend_height=self.DONT_EXTEND,
                        style=ThemeSelector.MUTED.style,
                    ),
                ]
            ),
            Condition(lambda: self.item is not self.NO_ITEM),
        )
        self.container = HSplit([self.inbox.container, form])

    @property
    def observation(self):
        return QuestionPresentation.observe(self.item)

    @property
    def is_approval(self):
        return bool(self.item and self.observation.request.kind == InteractionRequest.KIND_APPROVAL)

    @property
    def busy(self):
        key = SessionIdentity.current(self.view).to_tuple()
        return any(
            response.pending and SessionIdentity(identity).prefix() == key
            for identity, response in self.responses.items()
        )

    def begin_answer(self, option_id, value=None):
        response = AnswerFeedback.capture(self, option_id, value)
        self.responses[self.identity] = response
        self.leave()
        self.view.preparing.paint()
        return response

    def finish_answer(self, response, success):
        response.finish_pending()
        identity = response.identity
        current = SessionIdentity.current(self.view).to_tuple()
        if success or response.acknowledged:
            # Resume failure cannot undo an already accepted decision.
            response.acknowledge()
            self.drafts.pop(identity, self.NO_DRAFT)
            snapshot = HubSnapshot.last_observed(self.view)
            if snapshot and SessionIdentity(identity).prefix() == current == (
                snapshot.project_id,
                snapshot.selected_id,
            ):
                self.sync(snapshot.project_id, snapshot.selected_id, snapshot.questions)
        else:
            self.responses.pop(identity, self.NO_RESPONSE)
            if SessionIdentity(identity).prefix() == current:
                self.sync(*current, (response.item,))
        response.release_item()
        while len(self.responses) > self.MAX_RESPONSES:
            oldest = next(
                (key for key, value in self.responses.items() if not value.pending),
                self.NO_IDENTITY,
            )
            if oldest is self.NO_IDENTITY:
                break
            self.responses.pop(oldest)
        self.view.preparing.paint()

    def project(self, key, messages):
        return project_answers(
            [
                response
                for identity, response in self.responses.items()
                if SessionIdentity(identity).prefix() == key
            ],
            messages,
        )

    def leave(self):
        focus_input = self.inbox.focused or get_app().layout.current_control is self.manage_button.control
        if self.item:
            if not (self.observation.nonblocking and self.inbox.busy and not self.view.composer.text):
                self.drafts[self.identity] = self.view.composer.text
            from ...ui.view import HubView

            self.replace_input(HubView.composer_draft(self.view, self.EMPTY_TEXT))
        self.item, self.identity = self.NO_ITEM, self.NO_IDENTITY
        self.approval_options = []
        self.details.set_request(self.EMPTY_TEXT)
        if focus_input:
            get_app().layout.focus(self.view.composer)

    def return_to_input(self):
        self.inbox.hidden = True
        self.inbox.delivery = None
        self.leave()
        get_app().layout.focus(self.view.composer)

    def replace_input(self, text):
        from prompt_toolkit.document import Document
        self.view.composer.buffer.set_document(Document(text, len(text)), bypass_readonly=True)

    @property
    def answer_read_only(self):
        return bool(self.item and self.observation.nonblocking
                    and (self.inbox.busy or self.observation.response))

    def select(self, item, project_id, session_id):
        self.leave()
        self.inbox.delivery, self.inbox.status = None, ''
        if item:
            from ...ui.view import HubView
            HubView.store_composer_draft(self.view, self.view.composer.text)
            self.item = item
            self.identity = QuestionPresentation.identity(project_id, session_id, item)
            self.details.set_request(self.NEWLINE.join(self.detail_lines()))
            self.view.composer.buffer.cancel_completion()
            self.replace_input(self.inbox.answer_text(item) if self.observation.response
                               else self.drafts.get(self.identity, self.EMPTY_TEXT))
            self.load_approval_details()
            from ...ui.input.context import InputContext
            if InputContext(self.view).available and not self.view.settings_open:
                get_app().layout.focus(self.view.composer)

    def load_approval_details(self):
        if not self.observation.request.action or not self.view.on_approval_details:
            return
        identity = self.identity

        def loaded(text):
            if text is not None and self.identity == identity:
                self.details.set_request(text, reset_scroll=False)
                get_app().invalidate()

        self.view.on_approval_details(self.observation.request.to_record(), loaded)

    def sync(self, project_id, session_id, questions):
        self.inbox.sync(project_id, session_id, questions)
        def key(item):
            return QuestionPresentation.identity(project_id, session_id, item)

        blocking = [q for q in questions if not QuestionPresentation.observe(q).nonblocking
                    and key(q) not in self.responses]
        item = next((q for q in blocking if QuestionPresentation.observe(q).request.kind
                     == InteractionRequest.KIND_APPROVAL), self.NO_ITEM)
        if item is None and self.item and not self.inbox.hidden:
            # Keep a manually selected question across unrelated render updates.
            item = next((q for q in self.inbox.outstanding() if key(q) == self.identity), self.NO_ITEM)
        if item is None:
            item = self.inbox.candidate()
        if (project_id, session_id) in self.view.interrupting.pending:
            item = self.NO_ITEM
        if any(
            r.pending and SessionIdentity(k).prefix() == (project_id, session_id)
            for k, r in self.responses.items()
        ):
            item = self.NO_ITEM
        identity = key(item) if item else self.NO_IDENTITY
        if identity == self.identity:
            if item and self.observation.response != QuestionPresentation.observe(item).response:
                self.item = item
                if self.observation.response:
                    self.replace_input(self.inbox.answer_text(item))
            elif item:
                self.item = item
            return
        self.select(item, project_id, session_id)

    def text(self):
        """Plain equivalent for accessibility and diagnostics."""
        return (
            self.EMPTY_TEXT.join(
                ControlFragment.text(fragment) for fragment in self.options()
            )
            + self.NEWLINE
            + self.hint()
        )

    def heading(self):
        if not self.item:
            return self.EMPTY_TEXT
        key = (
            MessageKey.APPROVAL_HEADING
            if self.observation.request.kind == InteractionRequest.KIND_APPROVAL
            else MessageKey.QUESTION_HEADING
        )
        return self.INDENT + self.view.t(key)

    def options(self):
        return self.choices._get_text_fragments()

    def hint(self):
        if not self.item or self.busy:
            return self.EMPTY_TEXT
        if self.observation.nonblocking:
            return self.INDENT + self.view.t(MessageKey.QUESTION_SAVED_HINT if self.observation.response
                else MessageKey.QUESTION_SAVE_HINT)
        if self.immediate_choices:
            text = self.view.t(MessageKey.APPROVAL_NUMBER_HINT)
            if self.observation.request.action:
                text += ' • ' + self.view.t(MessageKey.APPROVAL_MANAGE_HINT)
            return self.INDENT + text
        key = MessageKey.APPROVAL_NUMBER_HINT if self.immediate_choices else (
            (
                MessageKey.QUESTION_TEXT_HINT
                if self.choices.labels()
                else MessageKey.QUESTION_FREE_TEXT_HINT
            )
            if self.observation.text_answer
            else MessageKey.QUESTION_CHOICE_HINT
        )
        text = self.INDENT + self.view.t(key)
        if self.observation.request.action:
            text += self.HINT_SEPARATOR + self.view.t(MessageKey.APPROVAL_MANAGE_HINT)
        return text

    def detail_lines(self):
        request = self.observation.request
        if request.action:
            from ...backend.interactions.approval import review_lines

            return review_lines(request.to_record(), self.view.t, include_risk=False)[1:]
        return [
            request.title,
            *([request.required_description] if request.description else []),
        ]

    def risk_fragments(self):
        """Keep the decision's risk directly above its choices, outside scrolling details."""
        if not self.item or not self.observation.request.action:
            return []
        from ...backend.interactions.approval import risk_label

        request = self.observation.request
        return [(f"bold fg:{self.view.theme.accent3}", self.INDENT + self.view.t(
            MessageKey.APPROVAL_RISK, value=risk_label(request.risk_scheme, request.risk)))]

    def tool_heading(self):
        if not self.item or not self.observation.request.action:
            return self.EMPTY_TEXT
        from llm.services.runtime.tool_approval_records import ToolApprovalActionView
        return self.INDENT + self.view.t(MessageKey.APPROVAL_TOOL,
            name=ToolApprovalActionView(self.observation.request.required_action).tool_or_empty)

    def open_manager(self):
        from .approval_manager import ApprovalManager
        if self.item:
            ApprovalManager(self.view, SessionIdentity(self.identity).project_id,
                            pending=True).open()

    def focus_actions(self):
        if self.item and not self.is_approval:
            get_app().layout.focus(self.inbox.destination if self.inbox.delivery else self.inbox.buttons[0])
        elif self.item and self.observation.request.action:
            get_app().layout.focus(self.manage_button)
        else:
            self.view.auxiliary.focus()

    def choices_for_request(self):
        if not self.item or self.observation.text_answer:
            return []
        # Parse once per request identity, not on every RadioList render.
        if not self.approval_options:
            self.approval_options = approval_choices(
                InteractionRequest.from_dict(self.observation.request.to_record()), self.view.t)
        return self.approval_options

    @property
    def immediate_choices(self):
        if not self.item or self.busy or self.observation.text_answer:
            return False
        request = self.observation.request
        return (request.kind == InteractionRequest.KIND_APPROVAL
                and request.input_schema is None
                and 0 < len(self.choices_for_request()) <= len(self.CHOICE_DIGITS))

    def choose_number(self, number):
        if self.immediate_choices and 1 <= int(number) <= len(self.choices_for_request()):
            self.view.composer.text = number
            self.answer()

    @property
    def can_move_choice(self):
        if not self.item or self.is_approval or self.answer_read_only:
            return False
        text, labels = self.view.composer.text.strip(), self.choices.labels()
        return bool(labels and (not text or text in {str(i) for i in range(1, len(labels) + 1)}))

    def move_choice(self, direction):
        if self.can_move_choice:
            text = self.view.composer.text.strip()
            index = int(text) + direction if text else 1
            self.replace_input(str(max(1, min(len(self.choices.labels()), index))))

    def answer(self):
        if self.busy or not self.item:
            return
        if self.observation.nonblocking:
            if self.inbox.busy:
                return
            if self.observation.response:
                self.inbox.prepare_delivery()
                return
        elif not self.view.on_answer:
            return
        text = self.view.composer.text.strip()
        if not text:
            return
        request = self.observation.request
        match = None
        if self.observation.text_answer:
            choices = QuestionSource.suggestions(request.source)
            value = (
                choices[int(text) - AnswerChoices.FIRST_NUMBER]
                if text.isdecimal()
                and AnswerChoices.FIRST_NUMBER <= int(text) <= len(choices)
                else text
            )
            option_id = request.options.at(0).id
        else:
            number, _, value_text = text.partition(AnswerChoices.VALUE_SEPARATOR)
            if (
                not number.isdecimal()
                or not AnswerChoices.FIRST_NUMBER
                <= int(number)
                <= len(self.choices_for_request())
            ):
                self.view.notice = self.view.t(MessageKey.QUESTION_CHOICE_HINT)
                return
            choice = self.choices_for_request()[int(number) - AnswerChoices.FIRST_NUMBER]
            option = next(request.options.at(index) for index in range(request.options.count)
                          if request.options.at(index).id == choice.option_id)
            option_id, value, match = option.id, self.NO_INPUT, choice.match
            if value_text and option.effect != InteractionOption.EFFECT_DENY:
                try:
                    schema = request.input_schema or {}
                    value = (
                        value_text
                        if schema.get(SchemaKeyword.TYPE) == SchemaType.STRING
                        else json.loads(value_text)
                    )
                except ValueError as error:
                    self.view.notice = str(error)
                    return
        if self.observation.nonblocking:
            self.inbox.save(self.item, option_id, value,
                            after=lambda saved: self.inbox.prepare_delivery())
        elif match is None:
            self.view.on_answer(self.item, option_id, value)
        else:
            from .approval_selection import ApprovalSelectionDialog
            ApprovalSelectionDialog(self, match, option_id, value).open()
