"""One independently cached thought or Tool row in an execution timeline."""

from ...model import TraceItem
from .render_contract import TranscriptLayout, ToolCaption
from ...config.theme_style import ThemeSelector

from rich.text import Text

from ...ui.output.markdown import HubMarkdown, make_console, _segment_style, _trim_right


def render_trace_item(item, width, theme, language):
    console = make_console(TranscriptLayout.inner_width(width), theme)
    if item.kind == TraceItem.TOOL:
        label = ToolCaption.render(item.text, item.status, language)
        rows = console.render_lines(
            Text(label), console.options, pad=TranscriptLayout.PAD
        )
        return tuple(
            (
                (
                    ThemeSelector.MUTED.style,
                    TranscriptLayout.INDENT
                    + TranscriptLayout.EMPTY.join(s.text for s in row),
                ),
            )
            for row in rows
        ) + (TranscriptLayout.BLANK_LINE,)
    rows = console.render_lines(
        HubMarkdown(item.text, theme), console.options, pad=TranscriptLayout.PAD
    )
    return tuple(
        (
            (TranscriptLayout.EMPTY, TranscriptLayout.INDENT),
            *_trim_right(
                [
                    (_segment_style(s.style, theme.code_background), s.text)
                    for s in row
                    if not s.control
                ]
            ),
        )
        for row in rows
    ) + (TranscriptLayout.BLANK_LINE,)
