"""Compact, shared thought-stream rendering for Tools and questions."""

from rich.text import Text

from ...model import TraceItem
from ...locales.keys import MessageKey
from ...config.theme_style import ThemeSelector
from ...ui.output.markdown import HubMarkdown, make_console
from .render_contract import TranscriptLayout, TranscriptPresentation, ToolCaption


class ExecutionPreview:
    TOOL_LINES = 4
    ELLIPSIS = '…'
    ANSWER_SENDING = 'answer_sending'

    @staticmethod
    def single_line(text):
        return ' '.join(text.split())

    @classmethod
    def fit(cls, text, width):
        value = Text(cls.single_line(text))
        value.truncate(width, overflow='ellipsis')
        return value.plain

    @classmethod
    def wrap(cls, text, width, limit, console):
        rows = Text(cls.single_line(text)).wrap(console, width, overflow='fold')
        if len(rows) > limit:
            rows[limit - 1].truncate(max(0, width - 1), overflow='crop')
            rows[limit - 1].append(cls.ELLIPSIS)
        return [row.plain for row in rows[:limit]]

    @classmethod
    def question(cls, item, details, width, console, theme, language):
        def plain(value):
            segments = console.render_lines(HubMarkdown(value, theme), console.options, pad=False)
            return cls.single_line(' '.join(''.join(s.text for s in line if not s.control) for line in segments))

        question = plain(details.get(MessageKey.QUESTION_HEADING, item.text))
        description = plain(details.get(MessageKey.CARD_DESCRIPTION, ''))
        rows = [cls.fit(question, width)]
        if description and description != question:
            rows.append(cls.fit(description, width))
        if MessageKey.CARD_ANSWER in details:
            answer = plain(details[MessageKey.CARD_ANSWER])
            if item.status == cls.ANSWER_SENDING:
                answer = language.status(item.status) + TranscriptPresentation.LABEL_SEPARATOR + answer
            rows.append(cls.fit(answer, width))
        if details.get(MessageKey.CARD_ERROR):
            rows.append(cls.fit(details[MessageKey.CARD_ERROR], width))
        return rows

    @classmethod
    def tool(cls, item, details, width, console, language):
        header = ToolCaption.render(item.text, item.status, language)
        if details.get(MessageKey.CARD_INPUT):
            header += TranscriptPresentation.LABEL_SEPARATOR + details[MessageKey.CARD_INPUT]
        result = details.get(MessageKey.CARD_ERROR) or details.get(MessageKey.TOOL_RESULT)
        rows = [cls.fit(header, width)]
        if result:
            rows.extend(cls.wrap(result, width, cls.TOOL_LINES - len(rows), console))
        return rows


def render_execution_preview(item, width, theme, language, *, resources=None):
    inner = TranscriptLayout.inner_width(width)
    console = resources.console(inner, theme) if resources else make_console(inner, theme)
    details = dict(item.details)
    rows = (ExecutionPreview.question(item, details, inner, console, theme, language)
            if item.kind == TraceItem.QUESTION
            else ExecutionPreview.tool(item, details, inner, console, language))
    return tuple(((ThemeSelector.MUTED.style, TranscriptLayout.INDENT + row),) for row in rows) + (
        TranscriptLayout.BLANK_LINE,)
