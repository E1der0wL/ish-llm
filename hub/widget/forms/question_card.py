"""Read-only request card; keyboard focus stays in the answer composer."""

from hub.locales.keys import MessageKey

from copy import copy
from prompt_toolkit.filters import Always

from rich.console import Group
from rich.text import Text

from ..output.text_viewport import TextViewport
from ...backend.interactions.approval import review_lines, risk_label
from ...ui.output.markdown import HubMarkdown, make_console, _segment_style


class QuestionCard(TextViewport):
    def __init__(self, question):
        self.question = question
        super().__init__("", tail=False, render_slot="question")
        self.window.right_margins = []
        self.window.dont_extend_height = Always()

    def set_request(self, text):
        """Never show a previous approval while the next card is rendering."""
        if self._resize_timer:
            self._resize_timer.cancel()
            self._resize_timer = None
        if self.render_worker:
            self.render_worker.cancel(self.render_slot)
        self.text = text
        self._requested_key = self._render_key = None
        self.top_line = self.left_column = 0
        # Immediate bounded fallback belongs to the new request. Rich wrapping
        # and Markdown parsing still happen on the shared render worker.
        self._apply_rows(self._prepare_rows(tuple((("", " " + line[:240]),)
            for line in text[:8000].splitlines()[:32]) or ((("", ""),),)))

    def render_request(self, width):
        view = self.question.view
        self.render_worker = view.render_worker
        theme, language = view.theme, copy(view.t)
        request = self.question.item["request"] if self.question.item else {}
        identity = self.question.identity
        def build():
            console = make_console(max(1, width - 2), theme)
            if request.get("action"):
                lines = review_lines(request, language)
                risk = language(MessageKey.APPROVAL_RISK, value=risk_label(request.get("risk_scheme"), request.get("risk")))
                parts = []
                for line in lines:
                    target = line.partition(":")[0] in {"path", "source", "destination", "cwd", "command", "argv", "workspace_id", "url"}
                    style = (f"{theme.code_foreground} on {theme.code_background}" if target else
                             f"bold {theme.notice}" if line == risk else
                             f"bold {theme.accent}" if line == lines[0] else theme.muted)
                    parts.append(Text(line, style=style, overflow="fold"))
                body = Group(*parts)
            else:
                body = HubMarkdown(request.get("title", "") + ("\n\n" + request["description"]
                    if request.get("description") else ""), theme)
            rows = console.render_lines(body, console.options, pad=False)
            return tuple((("", " "), *((_segment_style(s.style, theme.code_background), s.text)
                         for s in row if not s.control)) for row in rows) or ((("", ""),),)
        return (width, theme, identity, language.code, id(language.messages)), build

    def preferred_height(self, width, max_available_height, wrap_lines, get_line_prefix):
        return min(max_available_height, max(1, len(self.lines)))
