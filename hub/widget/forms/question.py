"""Questions and approvals reuse the chat composer without another focus target."""

from hub.locales.keys import MessageKey

import json
from llm.core.interactions import InteractionOption
from ...model import HubSnapshot, QuestionSource, QuestionPresentation, SessionIdentity
from ...config.theme_style import ThemeSelector
from ...backend.interactions.question_values import QuestionTool
from llm.core.schema import SchemaKeyword, SchemaType

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Window, HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from .question_card import QuestionCard
from ..input_contract import ControlFragment
from .answer_choices import AnswerChoices
from ..feedback.answer_feedback import AnswerFeedback, project_answers


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
    HEIGHT_DIVISOR = 3
    WRAP_LINES = True
    DONT_EXTEND = True

    def __init__(self, view):
        self.view = view
        self.item = self.NO_ITEM
        self.identity = self.NO_IDENTITY
        self.drafts = {}
        self.responses = {}
        self.details = QuestionCard(self)
        self.choices = AnswerChoices(self)
        self.details.window.height = lambda: Dimension(
            min=self.MINIMUM_HEIGHT,
            max=max(self.PREFERRED_HEIGHT, view._rows() // self.HEIGHT_DIVISOR),
        )
        self.container = ConditionalContainer(
            HSplit(
                [
                    Window(
                        FormattedTextControl(self.heading),
                        wrap_lines=self.WRAP_LINES,
                        dont_extend_height=self.DONT_EXTEND,
                        style=ThemeSelector.QUESTION_HEADING.style,
                    ),
                    self.details.window,
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

    @property
    def observation(self):
        return QuestionPresentation.observe(self.item)

    @property
    def busy(self):
        key = SessionIdentity.current(self.view).to_tuple()
        return any(
            response.pending and SessionIdentity(identity).prefix() == key
            for identity, response in self.responses.items()
        )

    def begin_answer(self, option_id):
        response = AnswerFeedback.capture(self, option_id)
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
        if self.item:
            self.drafts[self.identity] = self.view.composer.text
            from ...ui.view import HubView

            self.view.composer.text = HubView.composer_draft(self.view, self.EMPTY_TEXT)
        self.item, self.identity = self.NO_ITEM, self.NO_IDENTITY
        self.details.set_request(self.EMPTY_TEXT)

    def sync(self, project_id, session_id, questions):
        def key(item):
            return QuestionPresentation.identity(project_id, session_id, item)

        item = next(
            (q for q in questions if key(q) not in self.responses), self.NO_ITEM
        )
        if (project_id, session_id) in self.view.interrupting.pending:
            item = self.NO_ITEM
        if any(
            r.pending and SessionIdentity(k).prefix() == (project_id, session_id)
            for k, r in self.responses.items()
        ):
            item = self.NO_ITEM
        identity = key(item) if item else self.NO_IDENTITY
        if identity == self.identity:
            return
        self.leave()
        if item:
            from ...ui.view import HubView

            HubView.store_composer_draft(self.view, self.view.composer.text)
            self.item, self.identity = item, identity
            self.details.set_request(self.NEWLINE.join(self.detail_lines()))
            self.view.composer.buffer.cancel_completion()
            self.view.composer.text = self.drafts.get(identity, self.EMPTY_TEXT)

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
            if self.observation.request.action
            else MessageKey.QUESTION_HEADING
        )
        return self.INDENT + self.view.t(key)

    def options(self):
        return self.choices._get_text_fragments()

    def hint(self):
        if not self.item or self.busy:
            return self.EMPTY_TEXT
        key = (
            (
                MessageKey.QUESTION_TEXT_HINT
                if self.choices.labels()
                else MessageKey.QUESTION_FREE_TEXT_HINT
            )
            if self.observation.live
            else MessageKey.QUESTION_CHOICE_HINT
        )
        return (
            self.INDENT
            + self.view.t(key)
            + self.HINT_SEPARATOR
            + self.view.t(MessageKey.QUESTION_DETAILS_HINT)
        )

    def detail_lines(self):
        request = self.observation.request
        if request.action:
            from ...backend.interactions.approval import review_lines

            return review_lines(request.to_record(), self.view.t)
        return [
            request.title,
            *([request.required_description] if request.description else []),
        ]

    def review(self):
        if self.item and self.view.on_review:
            self.view.on_review(self.observation.request.to_record())

    def answer(self):
        if self.busy or not self.item or not self.view.on_answer:
            return
        text = self.view.composer.text.strip()
        if not text:
            return
        request = self.observation.request
        if self.observation.live:
            choices = QuestionSource.suggestions(request.source)
            value = (
                choices[int(text) - AnswerChoices.FIRST_NUMBER]
                if text.isdecimal()
                and AnswerChoices.FIRST_NUMBER <= int(text) <= len(choices)
                else text
            )
            option_id = QuestionTool.REPLY_ID
        else:
            number, _, value_text = text.partition(AnswerChoices.VALUE_SEPARATOR)
            if (
                not number.isdecimal()
                or not AnswerChoices.FIRST_NUMBER
                <= int(number)
                <= request.options.count
            ):
                self.view.notice = self.view.t(MessageKey.QUESTION_CHOICE_HINT)
                return
            option = request.options.at(int(number) - AnswerChoices.FIRST_NUMBER)
            option_id, value = option.id, self.NO_INPUT
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
        self.view.on_answer(self.item, option_id, value)
