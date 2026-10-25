"""Plain read-only text with scrolling independent of keyboard focus."""

from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import ScrollOffsets, UIContent, Window
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.application.current import get_app
import asyncio
import json

from ..controls import ScrollbarMargin
from .selection import SelectableControl
from ...ui.input.keys import KeyName
from ...ui.output.worker import RenderSlot
from ..input_contract import MouseResult
from typing import NamedTuple


class ViewportRenderKey:
    """Width prefix shared by all viewport keys; renderers may append context."""

    WIDTH_POSITION = 0

    @staticmethod
    def create(width, *context):
        return (width, *context)

    @classmethod
    def width(cls, key):
        return key[cls.WIDTH_POSITION]


class TextRows(NamedTuple):
    fragments: tuple
    lines: tuple
    maximum_width: int


class TextViewport(SelectableControl):
    ABSENT = None
    MARKDOWN = False
    FOLLOW_TAIL = True
    STOP_FOLLOWING = False
    EMPTY_TEXT = ""
    EMPTY_STYLE = ""
    FIRST_CELL = 0
    MIN_DIMENSION = 1
    WRAP_LINES = False
    SCROLL_OFFSET = 0
    DISPLAY_ARROWS = True
    ESCAPE_NON_ASCII = False
    JSON_INDENT = 2
    PAD_MARKDOWN = False
    RESIZE_DELAY = 0.075
    SHOW_CURSOR = False
    LEFT_STEP = -1
    RIGHT_STEP = 1
    UP_STEP = -1
    DOWN_STEP = 1
    PAGE_OVERLAP = 2

    def __init__(
        self,
        text,
        *,
        height=ABSENT,
        markdown=MARKDOWN,
        theme=ABSENT,
        tail=FOLLOW_TAIL,
        render_worker=ABSENT,
        render_slot=RenderSlot.POPUP
    ):
        super().__init__()
        self.text, self.markdown, self.theme = text, markdown, theme
        self._render_key = self.ABSENT
        self.render_worker = render_worker
        self.render_slot = render_slot
        self._requested_key = self.ABSENT
        self._resize_timer = self.ABSENT
        self.lines = (
            [self.EMPTY_TEXT]
            if render_worker
            else self._plain_text().splitlines() or [self.EMPTY_TEXT]
        )
        self.fragments = [[(self.EMPTY_STYLE, line)] for line in self.lines]
        self.top_line = self.left_column = self.FIRST_CELL
        self._width = self._height = self.MIN_DIMENSION
        self._tail = tail
        self._max_width = max(map(get_cwidth, self.lines))
        self.window = Window(
            self,
            height=height,
            wrap_lines=self.WRAP_LINES,
            get_vertical_scroll=lambda _: self.top_line,
            get_horizontal_scroll=lambda _: self.left_column,
            scroll_offsets=ScrollOffsets(
                top=self.SCROLL_OFFSET, bottom=self.SCROLL_OFFSET
            ),
            right_margins=[ScrollbarMargin(display_arrows=self.DISPLAY_ARROWS)],
        )

    @classmethod
    def plain_source(cls, text):
        value = text() if callable(text) else text
        return (
            value
            if isinstance(value, str)
            else json.dumps(
                value, ensure_ascii=cls.ESCAPE_NON_ASCII, indent=cls.JSON_INDENT
            )
        )

    def _plain_text(self):
        return self.plain_source(self.text)

    def render_request(self, width):
        theme = self.theme() if self.theme else self.ABSENT
        text, markdown = self.text, self.markdown

        def build():
            source = self.plain_source(text)
            if not markdown:
                return tuple(
                    ((self.EMPTY_STYLE, line),) for line in source.splitlines()
                ) or (((self.EMPTY_STYLE, self.EMPTY_TEXT),),)
            from ...ui.output.markdown import HubMarkdown, make_console, _segment_style

            console = make_console(max(self.MIN_DIMENSION, width), theme)
            return tuple(
                tuple(
                    (_segment_style(segment.style, theme.code_background), segment.text)
                    for segment in row
                )
                for row in console.render_lines(
                    HubMarkdown(source, theme), console.options, pad=self.PAD_MARKDOWN
                )
            ) or (((self.EMPTY_STYLE, self.EMPTY_TEXT),),)

        return ViewportRenderKey.create(width, theme, id(text)), build

    def create_content(self, width, height):
        key, build_rows = self.render_request(width)
        if key != self._requested_key:
            resized = (
                self._requested_key is not self.ABSENT
                and ViewportRenderKey.width(self._requested_key) != width
            )
            self._requested_key = key
            app = get_app()

            def apply(result):
                if self._requested_key == key:
                    self._apply_rows(result)
                    self._render_key = key
                    app.invalidate()

            def request():
                self._resize_timer = self.ABSENT
                if self.render_worker and app.is_running:
                    self.render_worker.request(
                        self.render_slot,
                        lambda: self._prepare_rows(build_rows()),
                        apply,
                        lambda _: self._prepare_rows(
                            [
                                [(self.EMPTY_STYLE, line)]
                                for line in self._plain_text().splitlines()
                            ]
                        ),
                    )
                else:
                    apply(self._prepare_rows(build_rows()))

            if self._resize_timer:
                self._resize_timer.cancel()
            if resized and self.render_worker and app.is_running:
                self.render_worker.cancel(self.render_slot)
                self._resize_timer = asyncio.get_running_loop().call_later(
                    self.RESIZE_DELAY, request
                )
            else:
                request()
        self._width, self._height = max(self.MIN_DIMENSION, width), max(
            self.MIN_DIMENSION, height
        )
        limit = max(self.FIRST_CELL, len(self.lines) - self._height)
        self.top_line = limit if self._tail else min(self.top_line, limit)
        self.left_column = min(
            self.left_column, max(self.FIRST_CELL, self._max_width - self._width)
        )
        if self._render_key == self._requested_key:
            self._tail = self.STOP_FOLLOWING
        return self.select_content(
            UIContent(
                get_line=lambda i: self.fragments[i],
                line_count=len(self.lines),
                cursor_position=Point(self.left_column, self.top_line),
                show_cursor=self.SHOW_CURSOR,
            ),
            width,
        )

    def _prepare_rows(self, rows):
        fragments = tuple(tuple(row) for row in rows) or (
            ((self.EMPTY_STYLE, self.EMPTY_TEXT),),
        )
        lines = tuple(
            self.EMPTY_TEXT.join(text for _, text in row) for row in fragments
        )
        return TextRows(fragments, lines, max(map(get_cwidth, lines)))

    def _apply_rows(self, result):
        self.fragments, self.lines, self._max_width = result

    def scroll(self, key):
        self._tail = self.STOP_FOLLOWING
        limit = max(self.FIRST_CELL, len(self.lines) - self._height)
        if key in (KeyName.LEFT, KeyName.RIGHT):
            self.left_column = max(
                self.FIRST_CELL,
                min(
                    max(self.FIRST_CELL, self._max_width - self._width),
                    self.left_column
                    + (self.LEFT_STEP if key == KeyName.LEFT else self.RIGHT_STEP),
                ),
            )
        elif key in (KeyName.HOME, KeyName.END):
            self.top_line = self.FIRST_CELL if key == KeyName.HOME else limit
        else:
            page = max(self.MIN_DIMENSION, self._height - self.PAGE_OVERLAP)
            self.top_line = max(
                self.FIRST_CELL,
                min(
                    limit,
                    self.top_line
                    + {
                        KeyName.UP: self.UP_STEP,
                        KeyName.DOWN: self.DOWN_STEP,
                        KeyName.PAGE_UP: -page,
                        KeyName.PAGE_DOWN: page,
                    }[key],
                ),
            )

    def scroll_to(self, line):
        self._tail = self.STOP_FOLLOWING
        self.top_line = max(
            self.FIRST_CELL,
            min(max(self.FIRST_CELL, len(self.lines) - self._height), line),
        )

    def mouse_handler(self, event):
        if event.event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
            self.scroll(
                KeyName.UP
                if event.event_type == MouseEventType.SCROLL_UP
                else KeyName.DOWN
            )
        elif event.event_type != MouseEventType.MOUSE_UP:
            return MouseResult.UNHANDLED

    def __pt_container__(self):
        return self.window
