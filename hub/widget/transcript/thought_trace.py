"""One independently cached thought or Tool row in an execution timeline."""

from rich.text import Text

from ...ui.output.markdown import HubMarkdown, make_console, _segment_style, _trim_right


def render_trace_item(item, width, theme, language):
    console = make_console(max(1, width - 4), theme)
    if item.kind == "tool":
        label = f"{language.icons.tool} {' '.join(item.text.split())[:64]} · {language.status(item.status)}"
        rows = console.render_lines(Text(label), console.options, pad=False)
        return tuple((("class:hub.muted", "  " + "".join(s.text for s in row)),) for row in rows) + ((("", ""),),)
    rows = console.render_lines(HubMarkdown(item.text, theme), console.options, pad=False)
    return tuple((("", "  "), *_trim_right([(_segment_style(s.style, theme.code_background), s.text)
                 for s in row if not s.control])) for row in rows) + ((("", ""),),)
