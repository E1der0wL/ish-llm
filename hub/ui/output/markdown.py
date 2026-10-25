"""Rich Markdown renderer with Hub colors and PTK fragment output."""

from io import StringIO
from .rich_terminal import RichTerminal
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
from ...config.theme_style import ThemeStyle


class RichMarkdownStyle:
    RULE = "markdown.hr"
    NONE = "none"
    BOLD = True
    ITALIC = True
    EMPTY = ""
    SPACE = " "
    ABSENT = None
    ATTRIBUTES = (
        ThemeStyle.BOLD,
        ThemeStyle.ITALIC,
        ThemeStyle.UNDERLINE,
        ThemeStyle.STRIKE,
    )
    RULE_ELEMENT = "hr"
    ACCENT = lambda theme: theme.accent
    USER = lambda theme: theme.user
    FOREGROUND = lambda theme: theme.foreground
    MUTED = lambda theme: theme.muted
    BOLD_SUFFIX = ThemeStyle.MODIFIER_SEPARATOR + ThemeStyle.BOLD
    ITALIC_SUFFIX = ThemeStyle.MODIFIER_SEPARATOR + ThemeStyle.ITALIC
    UNDERLINE_SUFFIX = ThemeStyle.MODIFIER_SEPARATOR + ThemeStyle.UNDERLINE
    THEME_RULES = (
        ("markdown.h1", ACCENT, BOLD_SUFFIX),
        ("markdown.h2", ACCENT, BOLD_SUFFIX),
        ("markdown.h3", ACCENT, BOLD_SUFFIX),
        ("markdown.h4", ACCENT, ITALIC_SUFFIX),
        ("markdown.link", ACCENT, UNDERLINE_SUFFIX),
        ("markdown.link_url", ACCENT, UNDERLINE_SUFFIX),
        ("markdown.code", USER, EMPTY),
        ("markdown.code_block", FOREGROUND, EMPTY),
        (RULE, MUTED, EMPTY),
        ("markdown.block_quote", MUTED, EMPTY),
        ("markdown.item.number", ACCENT, EMPTY),
        ("markdown.table.border", MUTED, EMPTY),
        ("markdown.table.header", ACCENT, BOLD_SUFFIX),
    )

    @classmethod
    def theme(cls, theme):
        return {
            name: reader(theme) + suffix if suffix else reader(theme)
            for name, reader, suffix in cls.THEME_RULES
        }


class HubSyntaxTheme(SyntaxTheme):
    def __init__(self, theme: HubTheme) -> None:
        self.theme = theme

    def get_style_for_token(self, token_type) -> RichStyle:
        if token_type in Token.Keyword:
            return RichStyle(color=self.theme.accent, bold=RichMarkdownStyle.BOLD)
        if token_type in Token.Literal:
            return RichStyle(color=self.theme.user)
        if token_type in Token.Comment:
            return RichStyle(color=self.theme.muted, italic=RichMarkdownStyle.ITALIC)
        return RichStyle(color=self.theme.code_foreground)

    def get_background_style(self) -> RichStyle:
        return RichStyle(
            color=self.theme.code_foreground, bgcolor=self.theme.code_background
        )


class HubHorizontalRule(HorizontalRule):
    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield Rule(
            style=console.get_style(
                RichMarkdownStyle.RULE, default=RichMarkdownStyle.NONE
            ),
            characters=icon.RULE,
        )
        yield Text()


class HubMarkdown(Markdown):
    def __init__(self, text: str, theme: HubTheme) -> None:
        super().__init__(text, code_theme=HubSyntaxTheme(theme))
        # Keep this override local; other Rich users retain their own rules.
        self.elements = {
            **self.elements,
            RichMarkdownStyle.RULE_ELEMENT: HubHorizontalRule,
        }


def _segment_style(
    style: RichStyle | None, code_background: str = HubTheme.DEFAULT_COLOR
) -> str:
    if style is RichMarkdownStyle.ABSENT:
        return RichMarkdownStyle.EMPTY
    parts = []
    if style.color is not RichMarkdownStyle.ABSENT and not style.color.is_default:
        parts.append(ThemeStyle.FOREGROUND_PREFIX + style.color.get_truecolor().hex)
    for attribute in RichMarkdownStyle.ATTRIBUTES:
        if getattr(style, attribute):
            parts.append(attribute)
    # Preserve only the explicit Hub code background; ordinary Markdown stays
    # transparent and third-party Rich themes cannot introduce other fills.
    if style.bgcolor is not RichMarkdownStyle.ABSENT and not style.bgcolor.is_default:
        background = style.bgcolor.get_truecolor().hex
        if background.lower() == code_background.lower():
            parts.append(ThemeStyle.BACKGROUND_PREFIX + background)
    return ThemeStyle.MODIFIER_SEPARATOR.join(parts)


def _trim_right(fragments):
    # Rich paragraphs and headings can contain fill cells even with pad=False.
    while fragments:
        style, text = fragments[-1]
        if ThemeStyle.BACKGROUND_PREFIX in style:
            break  # Keep filled code rows rectangular, including blank rows.
        trimmed = text.rstrip(RichMarkdownStyle.SPACE)
        if trimmed:
            fragments[-1] = (style, trimmed)
            break
        fragments.pop()
    return fragments


def make_console(inner, theme):
    console = RichTerminal.console(
        file=StringIO(), width=inner, theme=Theme(RichMarkdownStyle.theme(theme))
    )
    return console


class MarkdownRenderer:
    ABSENT = None
    PAD = False

    def __init__(self):
        self._console_key = self.ABSENT
        self._console = self.ABSENT

    def render(self, block, context):
        key = (context.width, context.theme)
        if key != self._console_key:
            self._console_key = key
            self._console = make_console(*key)
        console = self._console
        rendered = console.render_lines(
            HubMarkdown(block.text, context.theme), console.options, pad=self.PAD
        )
        return [
            _trim_right(
                [
                    (
                        _segment_style(segment.style, context.theme.code_background),
                        segment.text,
                    )
                    for segment in segments
                    if not segment.control
                ]
            )
            for segments in rendered
        ]
