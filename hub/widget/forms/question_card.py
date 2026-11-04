"""Read-only request card; keyboard focus stays in the answer composer."""
from hub.ui.output.worker import RenderSlot


from copy import copy
from prompt_toolkit.filters import Always

from rich.text import Text

from ..output.text_viewport import TextViewport
from ..controls import ScrollbarMargin
from prompt_toolkit.layout.margins import ConditionalMargin
from prompt_toolkit.filters import Condition
from ...model import QuestionPresentation
from llm.core.interactions import InteractionRequestObservation
from ...ui.output.markdown import HubMarkdown, make_console, _segment_style


class QuestionCard(TextViewport):
    EMPTY_TEXT = ""
    EMPTY_STYLE = ""
    INDENT = " "
    PARAGRAPH = "\n\n"
    FOLLOW_TAIL = False
    RENDER_SLOT = RenderSlot.QUESTION
    NO_RENDER = None
    MAX_LINE_CHARACTERS = 240
    MAX_CHARACTERS = 8000
    MAX_LINES = 32
    MINIMUM_WIDTH = 1
    HORIZONTAL_INSET = 2
    MINIMUM_HEIGHT = 1
    EMPHASIS_STYLE = "bold {color}"
    TEXT_OVERFLOW = "fold"
    PAD_RENDER = False

    def __init__(self, question):
        self.question = question
        super().__init__(
            self.EMPTY_TEXT, tail=self.FOLLOW_TAIL, render_slot=self.RENDER_SLOT
        )
        self.window.right_margins = [ConditionalMargin(
            ScrollbarMargin(display_arrows=True),
            Condition(lambda: bool(question.item and question.observation.request.action)),
        )]
        self.window.dont_extend_height = Always()

    def set_request(self, text, *, reset_scroll=True):
        """Never show a previous approval while the next card is rendering."""
        if self._resize_timer:
            self._resize_timer.cancel()
            self._resize_timer = self.NO_RENDER
        if self.render_worker:
            self.render_worker.cancel(self.render_slot)
        self.text = text
        self._requested_key = self._render_key = self.NO_RENDER
        if reset_scroll:
            self.top_line = self.left_column = 0
        # Immediate bounded fallback belongs to the new request. Rich wrapping
        # and Markdown parsing still happen on the shared render worker.
        request = self.question.observation.request if self.question.item else None
        styled = [(text[:self.MAX_CHARACTERS],
                   self.question.view.theme.muted if request and request.action else self.EMPTY_STYLE)]
        rows = [(line, style) for content, style in styled
                for line in content.splitlines()][:self.MAX_LINES]
        self._apply_rows(
            self._prepare_rows(
                tuple(
                    (
                        (
                            style,
                            self.INDENT + line[: self.MAX_LINE_CHARACTERS],
                        ),
                    )
                    for line, style in rows
                )
                or (((self.EMPTY_STYLE, self.EMPTY_TEXT),),)
            )
        )

    def render_request(self, width):
        view = self.question.view
        self.render_worker = view.render_worker
        theme, language = view.theme, copy(view.t)
        request = (
            QuestionPresentation.observe(self.question.item).request
            if self.question.item
            else InteractionRequestObservation({})
        )
        identity = self.question.identity
        text = self.text

        def build():
            console = make_console(
                max(self.MINIMUM_WIDTH, width - self.HORIZONTAL_INSET), theme
            )
            if request.action:
                body = Text(text, style=theme.muted, overflow=self.TEXT_OVERFLOW)
            else:
                body = HubMarkdown(
                    request.title_or_empty
                    + (
                        self.PARAGRAPH + request.required_description
                        if request.description
                        else self.EMPTY_TEXT
                    ),
                    theme,
                )
            rows = console.render_lines(body, console.options, pad=self.PAD_RENDER)
            return tuple(
                (
                    (self.EMPTY_STYLE, self.INDENT),
                    *(
                        (_segment_style(s.style, theme.code_background), s.text)
                        for s in row
                        if not s.control
                    ),
                )
                for row in rows
            ) or (((self.EMPTY_STYLE, self.EMPTY_TEXT),),)

        return (width, theme, identity, id(text), language.code, id(language.messages)), build

    def preferred_height(
        self, width, max_available_height, wrap_lines, get_line_prefix
    ):
        return min(max_available_height, max(self.MINIMUM_HEIGHT, len(self.lines)))
