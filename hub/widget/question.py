"""Questions and approvals reuse the chat composer without another focus target."""

import json

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension


class QuestionComposer:
    def __init__(self, view):
        self.view = view
        self.item = None
        self.identity = None
        self.drafts = {}
        self.busy = False
        self.container = ConditionalContainer(Window(FormattedTextControl(self.text), wrap_lines=True,
            height=lambda: Dimension(min=1, max=max(3, view._rows() // 3)), dont_extend_height=True,
            style="class:hub.composer"), Condition(lambda: self.item is not None))

    def leave(self):
        if self.item:
            self.drafts[self.identity] = self.view.composer.text
            self.view.composer.text = self.view._drafts.get(self.view._draft_key(self.view.selected), "")
        self.item, self.identity = None, None

    def sync(self, project_id, session_id, questions):
        item = questions[0] if questions else None
        identity = (project_id, session_id, item["request"]["id"]) if item else None
        if identity == self.identity:
            return
        self.leave()
        if item:
            self.view._drafts[self.view._draft_key(self.view.selected)] = self.view.composer.text
            self.item, self.identity, self.busy = item, identity, False
            self.view.composer.buffer.cancel_completion()
            self.view.composer.text = self.drafts.get(identity, "")

    def text(self):
        if not self.item:
            return ""
        request = self.item["request"]
        lines = [self.view.t("question_waiting"), request["title"]]
        if request.get("description"):
            lines.append(request["description"])
        if self.item["live"]:
            choices = request.get("source", {}).get("choices", [])
            lines.extend(f"{i}. {label}" for i, label in enumerate(choices, 1))
            lines.append(self.view.t("question_text_hint"))
        else:
            lines.extend(f"{i}. {option['label']}" for i, option in enumerate(request["options"], 1))
            lines.append(self.view.t("question_choice_hint"))
            if request.get("action"):
                lines.append(self.view.t("question_action_hint"))
        return "\n".join(lines)

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
            if value_text and option.get("effect") != "deny":
                try:
                    schema = request.get("input_schema") or {}
                    value = value_text if schema.get("type") == "string" else json.loads(value_text)
                except ValueError as error:
                    self.view.notice = str(error)
                    return
        self.view.on_answer(self.item, option_id, value)
