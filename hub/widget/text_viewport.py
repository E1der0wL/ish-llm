"""Plain read-only text with scrolling independent of keyboard focus."""

from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import ScrollOffsets, UIContent, UIControl, Window
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.application.current import get_app
import asyncio
import json

from .controls import ScrollbarMargin


class TextViewport(UIControl):
    def __init__(self, text, *, height=None, markdown=False, theme=None, tail=True, render_worker=None, render_slot="popup"):
        self.text, self.markdown, self.theme = text, markdown, theme
        self._render_key = None
        self.render_worker = render_worker
        self.render_slot = render_slot
        self._requested_key = None
        self._resize_timer = None
        self.lines = [""] if render_worker else self._plain_text().splitlines() or [""]
        self.fragments = [[("", line)] for line in self.lines]
        self.top_line = self.left_column = 0
        self._width = self._height = 1
        self._tail = tail
        self._max_width = max(map(get_cwidth, self.lines))
        self.window = Window(self, height=height, wrap_lines=False,
            get_vertical_scroll=lambda _: self.top_line, get_horizontal_scroll=lambda _: self.left_column,
            scroll_offsets=ScrollOffsets(top=0, bottom=0), right_margins=[ScrollbarMargin(display_arrows=True)])

    def _plain_text(self):
        value = self.text() if callable(self.text) else self.text
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)

    def render_request(self, width):
        theme = self.theme() if self.theme else None
        text, markdown = self.text, self.markdown
        def build():
            value = text() if callable(text) else text
            source = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            if not markdown:
                return tuple((("", line),) for line in source.splitlines()) or ((("", ""),),)
            from ..ui.output.markdown import HubMarkdown, make_console, _segment_style
            console = make_console(max(1, width), theme)
            return tuple(tuple((_segment_style(segment.style, theme.code_background), segment.text)
                               for segment in row) for row in console.render_lines(
                                   HubMarkdown(source, theme), console.options, pad=False)) or ((("", ""),),)
        return (width, theme, id(text)), build

    def create_content(self, width, height):
        key, build_rows = self.render_request(width)
        if key != self._requested_key:
            resized = self._requested_key is not None and self._requested_key[0] != width
            self._requested_key = key
            app = get_app()
            def apply(result):
                if self._requested_key == key:
                    self._apply_rows(result)
                    self._render_key = key
                    app.invalidate()
            def request():
                self._resize_timer = None
                if self.render_worker and app.is_running:
                    self.render_worker.request(self.render_slot, lambda: self._prepare_rows(build_rows()), apply,
                        lambda _: self._prepare_rows([[("", line)] for line in self._plain_text().splitlines()]))
                else:
                    apply(self._prepare_rows(build_rows()))
            if self._resize_timer:
                self._resize_timer.cancel()
            if resized and self.render_worker and app.is_running:
                self.render_worker.cancel(self.render_slot)
                self._resize_timer = asyncio.get_running_loop().call_later(.075, request)
            else:
                request()
        self._width, self._height = max(1, width), max(1, height)
        limit = max(0, len(self.lines) - self._height)
        self.top_line = limit if self._tail else min(self.top_line, limit)
        self.left_column = min(self.left_column, max(0, self._max_width - self._width))
        if self._render_key == self._requested_key:
            self._tail = False
        return UIContent(get_line=lambda i: self.fragments[i], line_count=len(self.lines),
                         cursor_position=Point(self.left_column, self.top_line), show_cursor=False)

    def _prepare_rows(self, rows):
        fragments = tuple(tuple(row) for row in rows) or ((("", ""),),)
        lines = tuple("".join(text for _, text in row) for row in fragments)
        return fragments, lines, max(map(get_cwidth, lines))

    def _apply_rows(self, result):
        self.fragments, self.lines, self._max_width = result

    def scroll(self, key):
        self._tail = False
        limit = max(0, len(self.lines) - self._height)
        if key in ("left", "right"):
            self.left_column = max(0, min(max(0, self._max_width - self._width),
                                          self.left_column + (-1 if key == "left" else 1)))
        elif key in ("home", "end"):
            self.top_line = 0 if key == "home" else limit
        else:
            page = max(1, self._height - 2)
            self.top_line = max(0, min(limit, self.top_line +
                {"up": -1, "down": 1, "pageup": -page, "pagedown": page}[key]))

    def mouse_handler(self, event):
        if event.event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
            self.scroll("up" if event.event_type == MouseEventType.SCROLL_UP else "down")
        elif event.event_type != MouseEventType.MOUSE_UP:
            return NotImplemented

    def __pt_container__(self):
        return self.window
