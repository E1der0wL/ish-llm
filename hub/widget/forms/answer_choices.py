"""A RadioList driven by the existing answer buffer, with no new focus area."""

from prompt_toolkit.filters import Always, Condition, Never
from prompt_toolkit.layout import ConditionalContainer, Window
from prompt_toolkit.widgets import Frame

from ..controls import RadioList


class AnswerChoices(RadioList):
    def __init__(self, question):
        self.question = question
        super().__init__([(None, "")], show_scrollbar=False,
                         checked_style="class:hub.question-choice-selected",
                         selected_style="", default_style="")
        self.control.focusable = Never()
        self.control.show_cursor = False
        self.window.style = "class:hub.question-choice"
        self.window.wrap_lines = Always()
        self.container = ConditionalContainer(
            Frame(self.window),
            Condition(lambda: bool(self.labels())), alternative_content=Window(height=1))

    def labels(self):
        if not self.question.item:
            return []
        request = self.question.item["request"]
        return (request.get("source", {}).get("choices", []) if self.question.item["live"]
                else [option["label"] for option in request.get("options", [])])

    def __pt_container__(self):
        return self.container

    def _get_text_fragments(self):
        question = self.question
        choices = self.labels()
        if not choices:
            self.current_value = None
            self._selected_index = -1
            return []
        self.values = [(str(i), f"{i}. {label}") for i, label in enumerate(choices, 1)]
        selected = question.view.composer.text.strip().partition(" ")[0]
        self.current_value = selected if any(key == selected for key, _ in self.values) else None
        self._selected_index = next((i for i, (key, _) in enumerate(self.values) if key == selected), -1)
        # Only the composer changes the selection. No mouse callbacks or
        # cursor marker from PTK's focusable list are needed for this display.
        return [(style, text) for style, text, *_ in super()._get_text_fragments()
                if "[SetCursorPosition]" not in style]
