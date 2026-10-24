"""A RadioList driven by the existing answer buffer, with no new focus area."""

from prompt_toolkit.filters import Always, Condition, Never
from prompt_toolkit.layout import ConditionalContainer, Window
from prompt_toolkit.widgets import Frame

from ..controls import RadioList
from ...model import QuestionSource, QuestionPresentation
from ...config.theme_style import ThemeSelector
from ..input_contract import ControlFragment


class AnswerChoices(RadioList):
    NO_VALUE = None
    UNSELECTED_INDEX = -1
    EMPTY_TEXT = ""
    FIRST_NUMBER = 1
    VALUE_SEPARATOR = " "
    LABEL_FORMAT = "{number}. {label}"
    EMPTY_HEIGHT = 1
    SHOW_SCROLLBAR = False
    SHOW_CURSOR = False

    def __init__(self, question):
        self.question = question
        super().__init__(
            [(self.NO_VALUE, self.EMPTY_TEXT)],
            show_scrollbar=self.SHOW_SCROLLBAR,
            checked_style=ThemeSelector.QUESTION_CHOICE_SELECTED.style,
            selected_style=self.EMPTY_TEXT,
            default_style=self.EMPTY_TEXT,
        )
        self.control.focusable = Never()
        self.control.show_cursor = self.SHOW_CURSOR
        self.window.style = ThemeSelector.QUESTION_CHOICE.style
        self.window.wrap_lines = Always()
        self.container = ConditionalContainer(
            Frame(self.window),
            Condition(lambda: bool(self.labels())),
            alternative_content=Window(height=self.EMPTY_HEIGHT),
        )

    def labels(self):
        if not self.question.item:
            return []
        item = QuestionPresentation.observe(self.question.item)
        request = item.request
        return (
            QuestionSource.suggestions(request.source)
            if item.live
            else request.options_or_empty.labels()
        )

    def __pt_container__(self):
        return self.container

    def _get_text_fragments(self):
        question = self.question
        choices = self.labels()
        if not choices:
            self.current_value = self.NO_VALUE
            self._selected_index = self.UNSELECTED_INDEX
            return []
        self.values = [
            (str(i), self.LABEL_FORMAT.format(number=i, label=label))
            for i, label in enumerate(choices, self.FIRST_NUMBER)
        ]
        selected = question.view.composer.text.strip().partition(self.VALUE_SEPARATOR)[
            0
        ]
        self.current_value = (
            selected
            if any(key == selected for key, _ in self.values)
            else self.NO_VALUE
        )
        self._selected_index = next(
            (i for i, (key, _) in enumerate(self.values) if key == selected),
            self.UNSELECTED_INDEX,
        )
        # Only the composer changes the selection. No mouse callbacks or
        # cursor marker from PTK's focusable list are needed for this display.
        return [
            (style, text)
            for style, text, *_ in super()._get_text_fragments()
            if ControlFragment.CURSOR_POSITION not in style
        ]
