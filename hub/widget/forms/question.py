"""Questions and approvals reuse the chat composer without another focus target."""

import json
from llm.core.interactions import InteractionOption

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Window, HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from .question_card import QuestionCard
from .answer_choices import AnswerChoices
from ..feedback.answer_feedback import AnswerFeedback, project_answers


class QuestionComposer:
    def __init__(self, view):
        self.view = view
        self.item = None
        self.identity = None
        self.drafts = {}
        self.responses = {}
        self.details = QuestionCard(self)
        self.choices = AnswerChoices(self)
        self.details.window.height = lambda: Dimension(min=1, max=max(3, view._rows() // 3))
        self.container = ConditionalContainer(HSplit([
            Window(FormattedTextControl(self.heading), wrap_lines=True, dont_extend_height=True,
                   style="class:hub.question-heading"),
            self.details.window,
            self.choices,
            Window(FormattedTextControl(self.hint), wrap_lines=True, dont_extend_height=True,
                   style="class:hub.muted"),
        ]), Condition(lambda: self.item is not None))

    @property
    def busy(self):
        key = (getattr(self.view, "project_id", ""), self.view.sessions[self.view.selected].id)
        return any(response.pending and identity[:2] == key for identity, response in self.responses.items())

    def begin_answer(self, option_id):
        response = AnswerFeedback.capture(self, option_id)
        self.responses[self.identity] = response
        self.leave()
        self.view.preparing.paint()
        return response

    def finish_answer(self, response, success):
        response.pending = False
        identity = response.identity
        current = (getattr(self.view, "project_id", ""), self.view.sessions[self.view.selected].id)
        if success or response.acknowledged:
            # Resume failure cannot undo an already accepted decision.
            response.acknowledged = True
            self.drafts.pop(identity, None)
            snapshot = getattr(self.view, "_last_snapshot", None)
            if snapshot and identity[:2] == current == (snapshot.project_id, snapshot.selected_id):
                self.sync(snapshot.project_id, snapshot.selected_id, snapshot.questions)
        else:
            self.responses.pop(identity, None)
            if identity[:2] == current:
                self.sync(*current, (response.item,))
        response.item = None
        while len(self.responses) > 128:
            oldest = next((key for key, value in self.responses.items() if not value.pending), None)
            if oldest is None:
                break
            self.responses.pop(oldest)
        self.view.preparing.paint()

    def project(self, key, messages):
        return project_answers([response for identity, response in self.responses.items() if identity[:2] == key], messages)

    def leave(self):
        if self.item:
            self.drafts[self.identity] = self.view.composer.text
            self.view.composer.text = self.view._drafts.get(self.view._draft_key(self.view.selected), "")
        self.item, self.identity = None, None
        self.details.set_request("")

    def sync(self, project_id, session_id, questions):
        def key(item):
            return project_id, session_id, item["request"]["id"], item["request"].get("revision", 1)
        item = next((q for q in questions if key(q) not in self.responses), None)
        if (project_id, session_id) in self.view.interrupting.pending:
            item = None
        if any(r.pending and k[:2] == (project_id, session_id) for k, r in self.responses.items()):
            item = None
        identity = key(item) if item else None
        if identity == self.identity:
            return
        self.leave()
        if item:
            self.view._drafts[self.view._draft_key(self.view.selected)] = self.view.composer.text
            self.item, self.identity = item, identity
            self.details.set_request("\n".join(self.detail_lines()))
            self.view.composer.buffer.cancel_completion()
            self.view.composer.text = self.drafts.get(identity, "")

    def text(self):
        """Plain equivalent for accessibility and diagnostics."""
        return "".join(fragment[1] for fragment in self.options()) + "\n" + self.hint()

    def heading(self):
        if not self.item:
            return ""
        key = "approval_heading" if self.item["request"].get("action") else "question_heading"
        return " " + self.view.t(key)

    def options(self):
        return self.choices._get_text_fragments()

    def hint(self):
        if not self.item or self.busy:
            return ""
        key = ("question_text_hint" if self.choices.labels() else "question_free_text_hint") if self.item["live"] else "question_choice_hint"
        return " " + self.view.t(key) + "\n " + self.view.t("question_details_hint")

    def detail_lines(self):
        request = self.item["request"]
        if request.get("action"):
            from ...backend.interactions.approval import review_lines
            return review_lines(request, self.view.t)
        return [request["title"], *([request["description"]] if request.get("description") else [])]

    def review(self):
        if self.item and self.view.on_review:
            self.view.on_review(self.item["request"])

    def answer(self):
        if self.busy or not self.item or not self.view.on_answer:
            return
        text = self.view.composer.text.strip()
        if not text:
            return
        request = self.item["request"]
        if self.item["live"]:
            choices = request.get("source", {}).get("choices", [])
            value = choices[int(text) - 1] if text.isdecimal() and 1 <= int(text) <= len(choices) else text
            option_id = "reply"
        else:
            number, _, value_text = text.partition(" ")
            if not number.isdecimal() or not 1 <= int(number) <= len(request["options"]):
                self.view.notice = self.view.t("question_choice_hint")
                return
            option = request["options"][int(number) - 1]
            option_id, value = option["id"], None
            if value_text and option.get("effect") != InteractionOption.EFFECT_DENY:
                try:
                    schema = request.get("input_schema") or {}
                    value = value_text if schema.get("type") == "string" else json.loads(value_text)
                except ValueError as error:
                    self.view.notice = str(error)
                    return
        self.view.on_answer(self.item, option_id, value)
