"""Read-only request card; keyboard focus stays in the answer composer."""

from hub.locales.keys import MessageKey

from copy import copy
from prompt_toolkit.filters import Always

from rich.console import Group
from rich.text import Text

from ..output.text_viewport import TextViewport
from ...backend.interactions.approval import review_lines, risk_label, ApprovalReview
from ...model import QuestionPresentation
from llm.core.interactions import InteractionRequestObservation
from ...ui.output.markdown import HubMarkdown, make_console, _segment_style


class QuestionCard(TextViewport):
    EMPTY_TEXT = ""
    EMPTY_STYLE = ""
    INDENT = " "
    PARAGRAPH = "\n\n"
    FOLLOW_TAIL = False
    RENDER_SLOT = "question"
    NO_RENDER = None
    MAX_LINE_CHARACTERS = 240
    MAX_CHARACTERS = 8000
    MAX_LINES = 32
    MINIMUM_WIDTH = 1
    HORIZONTAL_INSET = 2
    MINIMUM_HEIGHT = 1
    CODE_STYLE = "{foreground} on {background}"
    EMPHASIS_STYLE = "bold {color}"
    TEXT_OVERFLOW = "fold"
    PAD_RENDER = False

    def __init__(self, question):
        self.question = question
        super().__init__(
            self.EMPTY_TEXT, tail=self.FOLLOW_TAIL, render_slot=self.RENDER_SLOT
        )
        self.window.right_margins = []
        self.window.dont_extend_height = Always()

    def set_request(self, text):
        """Never show a previous approval while the next card is rendering."""
        if self._resize_timer:
            self._resize_timer.cancel()
            self._resize_timer = self.NO_RENDER
        if self.render_worker:
            self.render_worker.cancel(self.render_slot)
        self.text = text
        self._requested_key = self._render_key = self.NO_RENDER
        self.top_line = self.left_column = 0
        # Immediate bounded fallback belongs to the new request. Rich wrapping
        # and Markdown parsing still happen on the shared render worker.
        self._apply_rows(
            self._prepare_rows(
                tuple(
                    (
                        (
                            self.EMPTY_STYLE,
                            self.INDENT + line[: self.MAX_LINE_CHARACTERS],
                        ),
                    )
                    for line in text[: self.MAX_CHARACTERS].splitlines()[
                        : self.MAX_LINES
                    ]
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

        def build():
            console = make_console(
                max(self.MINIMUM_WIDTH, width - self.HORIZONTAL_INSET), theme
            )
            if request.action:
                lines = review_lines(request.to_record(), language)
                risk = language(
                    MessageKey.APPROVAL_RISK,
                    value=risk_label(request.risk_scheme, request.risk),
                )
                parts = []
                for line in lines:
                    target = ApprovalReview.target_line(line)
                    style = (
                        self.CODE_STYLE.format(
                            foreground=theme.code_foreground,
                            background=theme.code_background,
                        )
                        if target
                        else (
                            self.EMPHASIS_STYLE.format(color=theme.notice)
                            if line == risk
                            else (
                                self.EMPHASIS_STYLE.format(color=theme.accent)
                                if line == lines[0]
                                else theme.muted
                            )
                        )
                    )
                    parts.append(Text(line, style=style, overflow=self.TEXT_OVERFLOW))
                body = Group(*parts)
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

        return (width, theme, identity, language.code, id(language.messages)), build

    def preferred_height(
        self, width, max_available_height, wrap_lines, get_line_prefix
    ):
        return min(max_available_height, max(self.MINIMUM_HEIGHT, len(self.lines)))
