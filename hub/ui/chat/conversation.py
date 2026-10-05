"""Rich Markdown rendered into PTK cells, without printing to the terminal."""

from dataclasses import dataclass
from bisect import bisect_right
import re
from pathlib import Path
from prompt_toolkit.application.current import get_app
from ..output import OutputParser, RendererRegistry, ImageRenderer, RenderContext

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.layout import UIContent, UIControl, Window
from prompt_toolkit.layout import ScrollOffsets
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import get_cwidth
from rich.text import Text
from ..output.markdown import HubMarkdown, make_console, _segment_style, _trim_right

from ...config.theme import HubTheme
from ...locales import Language
from ...config.profile import UserProfile
from ...config.view_state import ReadingPosition
from ..widgets import ScrollbarMargin


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    text: str
    time: str = ""
    status: str = ""
    id: str = ""
    author: str = ""
    elapsed_seconds: float | None = None


def render_messages(messages: tuple[ChatMessage, ...], width: int, theme: HubTheme, language=None, *, anchors=None,
                    renderers=None, root=None, invalidate=None):
    """Return already-wrapped fragment lines using terminal-cell widths."""
    inner = max(1, width - 4)
    t = language or Language()
    console = make_console(inner, theme)
    lines = []
    account_name = UserProfile().display_name
    for index, message in enumerate(messages):
        if index and message.role == "user" and messages[index - 1].role in ("assistant", "reasoning"):
            lines.extend([[('', '')], [('', '')]])
        if anchors is not None:
            anchors.append((message.id or f"index:{index}", len(lines)))
        user = message.role == "user"
        inset = 1 if inner >= 3 else 0
        message_width = max(1, int(inner * 0.78) - 2 * inset) if user else inner
        label = (message.author or account_name) if user else t(
            "assistant" if message.role == "assistant" else
            "reasoning" if message.role == "reasoning" else "info")
        if message.status:
            label += " · " + t.status(message.status)
        if message.role == "assistant":
            label += " · " + t.elapsed(message.elapsed_seconds)
        elif message.time:
            label += " · " + message.time
        label = Text(label, overflow="fold")
        header_lines = ([] if message.role == "draft" else
                        console.render_lines(label, console.options.update(width=inner), pad=False))
        for segments in header_lines:
            text = "".join(segment.text for segment in segments)
            pad = max(0, inner - get_cwidth(text)) if user else 0
            lines.append([("", "  " + " " * pad),
                          ("class:hub.user bold" if user else "class:hub.accent bold", text)])
        renderable = (HubMarkdown(message.text, theme)
                      if message.role in ("assistant", "user", "draft", "reasoning")
                      else Text(message.text, overflow="fold"))
        def markdown_lines(value):
            rendered = console.render_lines(value, console.options.update(width=message_width), pad=False)
            return [_trim_right([(_segment_style(segment.style, theme.code_background), segment.text)
                                for segment in segments if not segment.control]) for segments in rendered]
        if renderers is not None and message.role == "assistant":
            blocks = OutputParser().parse(message.text, final=message.status not in ("streaming", "committed"))
            context = RenderContext(message_width, theme, t, Path(root or Path.cwd()), invalidate or get_app().invalidate)
            body = []
            for block in blocks:
                if anchors is not None and message.id:
                    anchors.append((f"{message.id}:block:{block.start}", len(lines) + len(body)))
                body.extend(renderers.render(block, context))
        else:
            body = markdown_lines(renderable)
        if user:
            body_width = max(1, max((sum(get_cwidth(text) for _, text in line) for line in body), default=0))
            block_width = body_width + 2 * inset
            padding = "  " + " " * max(0, inner - block_width)
            blank = [("", padding), ("class:hub.user-block", " " * block_width)]
            lines.append(blank)
            for fragments in body:
                length = sum(get_cwidth(text) for _, text in fragments)
                lines.append([("", padding), ("class:hub.user-block", " " * inset),
                              *(("class:hub.user-block " + style, text) for style, text in fragments),
                              ("class:hub.user-block", " " * (body_width - length + inset))])
            lines.append(blank)
        else:
            lines.extend([("", "  "), *fragments] for fragments in body)
        lines.extend([[('', '')], [('', '')]])
    return lines or [[("", "")]]


class ConversationControl(UIControl):
    def __init__(self, messages: tuple[ChatMessage, ...], theme: HubTheme, language=None) -> None:
        self.messages = messages
        self.theme = theme
        self.language = language or Language()
        self.file_root = Path.cwd()
        self.renderers = RendererRegistry()
        self.images = ImageRenderer()
        self.renderers.register("hub-image", self.images)
        self.cursor_line = 0
        self.top_line = 0
        self.left_column = 0
        self._width = 1
        self._height = 1
        self.follow_tail = False
        self._cache_key = None
        self._lines = [[("", "")]]
        self._anchors = []
        self._pending_position = None
        self.on_position_changed = None
        self.search_query = ""
        self.search_index = -1
        self.search_hits = []
        self._search_lines = {}
        self._search_key = None

    def _index_search(self) -> None:
        key = (self._cache_key, self.search_query)
        if key == self._search_key:
            return
        self._search_key = key
        self.search_hits = []
        self._search_lines = {}
        if self.search_query:
            # A phrase may cross a wrapped line; whitespace in the query matches
            # display whitespace while positions still refer to rendered rows.
            pattern = re.compile(re.escape(self.search_query).replace(r"\ ", r"\s+"), re.IGNORECASE)
            texts, starts, offset = [], [], 0
            for row, fragments in enumerate(self._lines):
                text = "".join(fragment[1] for fragment in fragments)
                starts.append(offset)
                texts.append(text)
                offset += len(text) + 1
            for match in pattern.finditer("\n".join(texts)):
                first = max(0, bisect_right(starts, match.start()) - 1)
                last = max(first, bisect_right(starts, match.end() - 1) - 1)
                index = len(self.search_hits)
                self.search_hits.append((first, match.start() - starts[first], match.end() - starts[first]))
                for row in range(first, last + 1):
                    start, end = max(0, match.start() - starts[row]), min(len(texts[row]), match.end() - starts[row])
                    self._search_lines.setdefault(row, []).append((index, start, end))
        self.search_index = min(self.search_index, len(self.search_hits) - 1)

    def set_search(self, query: str) -> None:
        self.search_query = query
        self.search_index = -1
        self._index_search()
        if self.search_hits:
            self.move_search()

    def move_search(self, direction: int = 1) -> None:
        self._index_search()
        if not self.search_hits:
            return
        self.search_index = (self.search_index + direction) % len(self.search_hits)
        row, start, _ = self.search_hits[self.search_index]
        self._scroll_to(row)
        text = "".join(fragment[1] for fragment in self._lines[row])
        column = get_cwidth(text[:start])
        self.left_column = max(0, column - self._width + 1) if column >= self._width else 0

    def _search_line(self, row):
        hits = self._search_lines.get(row)
        if not hits:
            return self._lines[row]
        result, offset = [], 0
        for style, text in self._lines[row]:
            stop = offset + len(text)
            cursor = offset
            for index, start, end in hits:
                left, right = max(start, offset), min(end, stop)
                if left >= right:
                    continue
                if cursor < left:
                    result.append((style, text[cursor - offset:left - offset]))
                highlight = "class:hub.search.current" if index == self.search_index else "class:hub.search.match"
                result.append((style + " " + highlight, text[left - offset:right - offset]))
                cursor = right
            if cursor < stop:
                result.append((style, text[cursor - offset:]))
            offset = stop
        return result

    def scroll(self, key: str) -> None:
        if key in ("left", "right"):
            limit = max(0, max(map(fragment_list_width, self._lines), default=0) - self._width)
            self.left_column = max(0, min(limit, self.left_column + (-1 if key == "left" else 1)))
        elif key == "home":
            self._scroll_to(0)
        elif key == "end":
            self._scroll_to(len(self._lines))
            self.cursor_line = len(self._lines) - 1
        else:
            page = max(1, self._height - 2)
            delta = {"up": -1, "down": 1, "pageup": -page, "pagedown": page}[key]
            self._scroll_to(self.top_line + delta)

    def _scroll_to(self, line):
        self._pending_position = None
        self.follow_tail = False
        self.top_line = max(0, min(max(0, len(self._lines) - self._height), line))
        self.cursor_line = self.top_line
        if self.on_position_changed:
            self.on_position_changed()

    @property
    def restoring(self):
        return self._pending_position is not None

    def reading_position(self):
        if self._pending_position is not None:
            return self._pending_position
        message_id, start = next(((identifier, line) for identifier, line in reversed(self._anchors)
                                 if line <= self.top_line), ("", 0))
        return ReadingPosition(message_id, self.top_line - start, self.top_line)

    def restore_position(self, position):
        self._pending_position = position
        self.follow_tail = False

    def is_focusable(self) -> bool:
        return False

    def create_content(self, width: int, height: int) -> UIContent:
        self._height = max(1, height)
        self._width = max(1, width)
        key = (width, self.messages, self.theme, self.language.code, str(self.file_root), self.renderers.version)
        if self._cache_key != key:
            # Keep the same message in view across wrapping/theme changes.
            position = self.reading_position() if self._cache_key is not None else None
            self._anchors = []
            self._lines = render_messages(self.messages, width, self.theme, self.language, anchors=self._anchors,
                                          renderers=self.renderers, root=self.file_root, invalidate=get_app().invalidate)
            self._cache_key = key
            self.left_column = min(self.left_column, max(0,
                max(map(fragment_list_width, self._lines), default=0) - self._width))
            if position is not None and not self.follow_tail and not self.restoring:
                self._pending_position = position
        if self._pending_position is not None and self.messages:
            position = self._pending_position
            starts = dict(self._anchors)
            self.top_line = (starts[position.message_id] + position.offset
                             if position.message_id in starts else position.line)
            self.cursor_line = self.top_line
            self._pending_position = None
        self.cursor_line = min(self.cursor_line, len(self._lines) - 1)
        self.top_line = min(self.top_line, max(0, len(self._lines) - self._height))
        if self.follow_tail:
            self.cursor_line = len(self._lines) - 1
            self.top_line = max(0, len(self._lines) - self._height)
            self.follow_tail = False
        self._index_search()
        return UIContent(get_line=self._search_line, line_count=len(self._lines),
                         cursor_position=Point(x=self.left_column, y=self.cursor_line), show_cursor=False)

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
            self.follow_tail = False
            delta = -3 if mouse_event.event_type == MouseEventType.SCROLL_UP else 3
            self._scroll_to(self.top_line + delta)
        elif mouse_event.event_type == MouseEventType.MOUSE_UP:
            return None
        else:
            return NotImplemented


class ConversationView:
    def __init__(self, messages: tuple[ChatMessage, ...], theme: HubTheme, language=None) -> None:
        self.control = ConversationControl(messages, theme, language)
        self.window = Window(self.control, style="class:hub", wrap_lines=False,
                             get_vertical_scroll=lambda window: self.control.top_line,
                             get_horizontal_scroll=lambda window: self.control.left_column,
                             scroll_offsets=ScrollOffsets(top=0, bottom=0),
                             right_margins=[ScrollbarMargin(display_arrows=True)])

    def __pt_container__(self):
        return self.window

    def set_messages(self, messages: tuple[ChatMessage, ...], position=None) -> None:
        self.control.images.reset()
        self.control.set_search("")
        self.control.messages = messages
        self.control.cursor_line = 0
        self.control.top_line = 0
        self.control.left_column = 0
        self.window.vertical_scroll = 0
        self.control._cache_key = None
        self.control._anchors = []
        self.control.restore_position(position)
