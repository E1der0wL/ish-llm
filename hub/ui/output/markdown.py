"""Rich Markdown renderer with Hub colors and PTK fragment output."""

from io import StringIO
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import HorizontalRule, Markdown
from rich.rule import Rule
from rich.style import Style as RichStyle
from rich.syntax import SyntaxTheme
from rich.text import Text
from rich.theme import Theme
from pygments.token import Token
from ...config.theme import HubTheme
from ...asset import icon

class HubSyntaxTheme(SyntaxTheme):
    def __init__(self, theme: HubTheme) -> None:
        self.theme = theme

    def get_style_for_token(self, token_type) -> RichStyle:
        if token_type in Token.Keyword:
            return RichStyle(color=self.theme.accent, bold=True)
        if token_type in Token.Literal:
            return RichStyle(color=self.theme.user)
        if token_type in Token.Comment:
            return RichStyle(color=self.theme.muted, italic=True)
        return RichStyle(color=self.theme.code_foreground)

    def get_background_style(self) -> RichStyle:
        return RichStyle(color=self.theme.code_foreground, bgcolor=self.theme.code_background)


class HubHorizontalRule(HorizontalRule):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield Rule(style=console.get_style("markdown.hr", default="none"), characters=icon.RULE)
        yield Text()


class HubMarkdown(Markdown):
    def __init__(self, text: str, theme: HubTheme) -> None:
        super().__init__(text, code_theme=HubSyntaxTheme(theme))
        # Keep this override local; other Rich users retain their own rules.
        self.elements = {**self.elements, "hr": HubHorizontalRule}


def _segment_style(style: RichStyle | None, code_background: str = "default") -> str:
    if style is None:
        return ""
    parts = []
    if style.color is not None and not style.color.is_default:
        parts.append("fg:" + style.color.get_truecolor().hex)
    for attribute in ("bold", "italic", "underline", "strike"):
        if getattr(style, attribute):
            parts.append(attribute)
    # Preserve only the explicit Hub code background; ordinary Markdown stays
    # transparent and third-party Rich themes cannot introduce other fills.
    if style.bgcolor is not None and not style.bgcolor.is_default:
        background = style.bgcolor.get_truecolor().hex
        if background.lower() == code_background.lower():
            parts.append("bg:" + background)
    return " ".join(parts)


def _trim_right(fragments):
    # Rich paragraphs and headings can contain fill cells even with pad=False.
    while fragments:
        style, text = fragments[-1]
        if "bg:" in style:
            break  # Keep filled code rows rectangular, including blank rows.
        trimmed = text.rstrip(" ")
        if trimmed:
            fragments[-1] = (style, trimmed)
            break
        fragments.pop()
    return fragments


def make_console(inner, theme):
    console = Console(file=StringIO(), width=inner, color_system="truecolor",
                      force_terminal=True, highlight=False,
                      theme=Theme({"markdown.h1": theme.accent + " bold",
                                   "markdown.h2": theme.accent + " bold",
                                   "markdown.h3": theme.accent + " bold",
                                   "markdown.h4": theme.accent + " italic",
                                   "markdown.link": theme.accent + " underline",
                                   "markdown.link_url": theme.accent + " underline",
                                   "markdown.code": theme.user,
                                   "markdown.code_block": theme.foreground,
                                   "markdown.hr": theme.muted,
                                   "markdown.block_quote": theme.muted,
                                   "markdown.item.number": theme.accent,
                                   "markdown.table.border": theme.muted,
                                   "markdown.table.header": theme.accent + " bold"}))
    return console

class MarkdownRenderer:
    def render(self, block, context):
        console = make_console(context.width, context.theme)
        rendered = console.render_lines(HubMarkdown(block.text, context.theme), console.options, pad=False)
        return [_trim_right([(_segment_style(segment.style, context.theme.code_background), segment.text)
                             for segment in segments if not segment.control]) for segments in rendered]
