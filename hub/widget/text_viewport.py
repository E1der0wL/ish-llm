"""Plain read-only text with scrolling independent of keyboard focus."""

from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import ScrollOffsets, UIContent, UIControl, Window
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import get_cwidth

from .controls import ScrollbarMargin


class TextViewport(UIControl):
    def __init__(self, text, *, height=None, markdown=False, theme=None, tail=True):
        self.text, self.markdown, self.theme = text, markdown, theme
        self._render_key = None
        self.lines = text.splitlines() or [""]
        self.fragments = [[("", line)] for line in self.lines]
        self.top_line = self.left_column = 0
        self._width = self._height = 1
        self._tail = tail
        self._max_width = max(map(get_cwidth, self.lines))
        self.window = Window(self, height=height, wrap_lines=False,
            get_vertical_scroll=lambda _: self.top_line, get_horizontal_scroll=lambda _: self.left_column,
            scroll_offsets=ScrollOffsets(top=0, bottom=0), right_margins=[ScrollbarMargin(display_arrows=True)])

    def create_content(self, width, height):
        if self.markdown:
            from ..ui.output.markdown import HubMarkdown, make_console, _segment_style
            theme = self.theme()
            key = (width, theme)
            if key != self._render_key:
                console = make_console(max(1, width), theme)
                self.fragments = [[(_segment_style(segment.style, theme.code_background), segment.text)
                                   for segment in row] for row in console.render_lines(
                                       HubMarkdown(self.text, theme), console.options, pad=False)] or [[("", "")]]
                self.lines = ["".join(text for _, text in row) for row in self.fragments]
                self._max_width = max(map(get_cwidth, self.lines))
                self._render_key = key
        self._width, self._height = max(1, width), max(1, height)
        limit = max(0, len(self.lines) - self._height)
        self.top_line = limit if self._tail else min(self.top_line, limit)
        self.left_column = min(self.left_column, max(0, self._max_width - self._width))
        self._tail = False
        return UIContent(get_line=lambda i: self.fragments[i], line_count=len(self.lines),
                         cursor_position=Point(self.left_column, self.top_line), show_cursor=False)

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
