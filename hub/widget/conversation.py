"""Rich Markdown rendered into PTK cells, without printing to the terminal."""

from ..model import ChatMessage
from ..asset.icon import for_style
from bisect import bisect_right
import asyncio
from copy import copy
from time import perf_counter
from pathlib import Path
from prompt_toolkit.application.current import get_app
from ..ui.output import OutputParser, OutputObject, RendererRegistry, ImageRenderer, RenderContext

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.layout import UIContent, UIControl, Window
from prompt_toolkit.layout import ScrollOffsets
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import get_cwidth
from rich.text import Text
from ..ui.output.markdown import HubMarkdown, make_console, _segment_style, _trim_right

from ..config.theme import HubTheme
from ..locales import Language
from ..config.profile import UserProfile
from ..config.view_state import ReadingPosition
from .controls import ScrollbarMargin


def render_messages(messages: tuple[ChatMessage, ...], width: int, theme: HubTheme, language=None, *, anchors=None,
                    renderers=None, root=None, invalidate=None, objects=None, index_offset=0,
                    section="all", blocks=None, render_block=None):
    """Return already-wrapped fragment lines using terminal-cell widths."""
    inner = max(1, width - 4)
    t = language or Language(icons=for_style(theme.icon_style))
    console = make_console(inner, theme)
    lines = []
    account_name = UserProfile().display_name
    for local_index, message in enumerate(messages):
        index = local_index + index_offset
        if local_index and message.role == "user" and messages[local_index - 1].role in ("assistant", "reasoning"):
            lines.extend([[('', '')], [('', '')]])
        if anchors is not None and section != "body":
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
        header_lines = ([] if message.role == "draft" or section == "body" else
                        console.render_lines(label, console.options.update(width=inner), pad=False))
        for segments in header_lines:
            text = "".join(segment.text for segment in segments)
            pad = max(0, inner - get_cwidth(text)) if user else 0
            lines.append([("", "  " + " " * pad),
                          ("class:hub.user bold" if user else "class:hub.accent bold", text)])
        if section == "header":
            continue
        def markdown_lines(value):
            rendered = console.render_lines(value, console.options.update(width=message_width), pad=False)
            return [_trim_right([(_segment_style(segment.style, theme.code_background), segment.text)
                                for segment in segments if not segment.control]) for segments in rendered]
        if renderers is not None and message.role == "assistant":
            parsed = blocks if blocks is not None else OutputParser().parse(message.text, final=message.status not in ("streaming", "committed"))
            context = RenderContext(message_width, theme, t, Path(root or Path.cwd()), invalidate or get_app().invalidate)
            body = []
            for block in parsed:
                identifier = f"{message.id or f'index:{index}'}:block:{block.start}"
                if anchors is not None and message.id:
                    anchors.append((identifier, len(lines) + len(body)))
                rendered = (render_block or renderers.render)(block, context)
                if objects is not None and renderers.is_object(block):
                    title = dict(block.attributes).get("title") or dict(block.attributes).get("alt") or block.kind.removeprefix("hub-")
                    objects.append(OutputObject(identifier, message.id, block, title,
                        tuple(tuple(row) for row in rendered), len(lines) + len(body), len(lines) + len(body) + len(rendered)))
                body.extend(rendered)
        else:
            renderable = (HubMarkdown(message.text, theme)
                          if message.role in ("assistant", "user", "draft", "reasoning")
                          else Text(message.text, overflow="fold"))
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
    return lines if section == "header" else lines or [[("", "")]]


class ConversationControl(UIControl):
    def __init__(self, messages: tuple[ChatMessage, ...], theme: HubTheme, language=None) -> None:
        self.messages = messages
        self.theme = theme
        self.language = language or Language(icons=lambda: for_style(self.theme.icon_style))
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
        from .message_cache import MessageRenderCache
        self._message_cache = MessageRenderCache()
        self._lines = [[("", "")]]
        self._anchors = []
        self._anchor_starts = {}
        self.objects = []
        self._pending_position = None
        self.on_position_changed = None
        self.search_query = ""
        self.search_index = -1
        self.search_hits = []
        self._search_lines = {}
        self._search_key = None
        self.render_worker = None
        self.render_slot = "transcript"
        self._requested_key = None
        self._resize_timer = None
        self._max_width = 0
        self.content_calls = 0
        self.content_seconds = self.content_max_seconds = 0.0
        self._app = None
        self._deferred_scroll = None

    def _application(self):
        app = get_app()
        if app.is_running:
            self._app = app
        return self._app if self._app is not None and self._app.is_running else app

    @property
    def messages(self):
        return self._messages

    @messages.setter
    def messages(self, value):
        if value is not getattr(self, "_messages", None):
            self._messages = value
            self._message_revision = getattr(self, "_message_revision", 0) + 1

    def _request_frame(self, width, key):
        messages, theme, language, root = self.messages, self.theme, self.language, self.file_root
        language = copy(language)
        language.icon_source = for_style(theme.icon_style)
        app = self._application()
        def build():
            return self._message_cache.prepare(messages, width, theme, language, render=render_messages,
                renderers=self.renderers, root=root, invalidate=app.invalidate)
        def fallback(error):
            from .message_cache import Frame, frozen_rows
            from ..ui.output.registry import literal_lines
            rows = frozen_rows(literal_lines("\n\n".join(m.text for m in messages), max(1, width)))
            return Frame(rows, (), (), max(map(fragment_list_width, rows), default=0))
        def apply(frame):
            if (self._requested_key is None or key[0] != self._requested_key[0] or
                    key[5:] != self._requested_key[5:] or
                    key[2] != self.theme or key[3] != id(self.language) or key[4] != str(self.file_root)):
                return
            position = self.reading_position() if self._cache_key is not None else None
            self._lines, self._anchors, self.objects = frame.lines, frame.anchors, frame.objects
            self._max_width = frame.max_width
            self._anchor_starts = frame.starts
            self._cache_key = key
            if position is not None and not self.follow_tail and not self.restoring:
                self._pending_position = position
            self._resolve_position(partial=True)
            if self._deferred_scroll is not None:
                base, vertical, horizontal = self._deferred_scroll
                self._deferred_scroll = None
                self._scroll_to((len(self._lines) - self._height if base == "end" else 0) + vertical)
                self.left_column = max(0, min(max(0, self._max_width - self._width), horizontal))
            app.invalidate()
        if self.render_worker is not None and app.is_running:
            self.render_worker.request(self.render_slot, build, apply, fallback, progressive=True)
        else:
            apply(build())

    def _index_search(self) -> None:
        key = (self._cache_key, self.search_query)
        if key == self._search_key:
            return
        self._search_key = key
        self.search_hits = []
        self._search_lines = {}
        if not self.search_query:
            if self.render_worker:
                self.render_worker.cancel("search")
            return
        from .search_index import index_lines
        lines, query = self._lines, self.search_query
        app = self._application()
        def apply(result):
            if self._search_key != key or self._cache_key != key[0]:
                return
            self.search_hits, self._search_lines = result
            self.search_index = min(self.search_index, len(self.search_hits) - 1)
            if self.search_index == -1 and self.search_hits:
                self.move_search()
            app.invalidate()
        if self.render_worker and app.is_running:
            self.render_worker.request("search", lambda: index_lines(lines, query), apply, lambda _: ((), {}))
        else:
            self.search_hits, self._search_lines = index_lines(lines, query)
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
        # A snapshot can arrive between paints. Apply navigation to its current
        # layout, not the previous session's empty or differently wrapped rows.
        self.create_content(self._width, self._height)
        if self._cache_key is None:
            base, vertical, horizontal = self._deferred_scroll or ("home", 0, 0)
            if key in ("home", "end"):
                base, vertical = key, 0
            elif key in ("left", "right"):
                horizontal += -1 if key == "left" else 1
            else:
                page = max(1, self._height - 2)
                vertical += {"up": -1, "down": 1, "pageup": -page, "pagedown": page}[key]
            self._deferred_scroll = base, vertical, horizontal
            self._pending_position = None
            self.follow_tail = False
            return
        if key in ("left", "right"):
            limit = max(0, self._max_width - self._width)
            self.left_column = max(0, min(limit, self.left_column + (-1 if key == "left" else 1)))
        elif key == "home":
            self._scroll_to(0)
        elif key == "end":
            self._scroll_to(len(self._lines))
            self.cursor_line = len(self._lines) - 1
            self.follow_tail = self._cache_key != self._requested_key
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
        index = bisect_right(self._anchors, self.top_line, key=lambda item: item[1]) - 1
        message_id, start = self._anchors[index] if index >= 0 else ("", 0)
        return ReadingPosition(message_id, self.top_line - start, self.top_line)

    def restore_position(self, position):
        self._pending_position = position
        self.follow_tail = False

    def is_focusable(self) -> bool:
        return False

    def create_content(self, width: int, height: int) -> UIContent:
        started = perf_counter()
        self._height = max(1, height)
        self._width = max(1, width)
        app = self._application()
        loop_id = id(asyncio.get_running_loop()) if app.is_running else 0
        key = (width, self._message_revision, self.theme, id(self.language), str(self.file_root), self.renderers.version, loop_id)
        if self._requested_key != key:
            resized = self._requested_key is not None and self._requested_key[0] != width
            self._requested_key = key
            if self._resize_timer and resized:
                self._resize_timer.cancel()
                self._resize_timer = None
            if resized and self.render_worker is not None and app.is_running:
                self.render_worker.cancel(self.render_slot)
                def resized_frame():
                    self._resize_timer = None
                    self._request_frame(self._width, self._requested_key)
                    app.invalidate()
                self._resize_timer = asyncio.get_running_loop().call_later(.075, resized_frame)
            elif self._resize_timer is None:
                self._request_frame(width, key)
        self._resolve_position()
        self._index_search()
        content = UIContent(get_line=self._search_line, line_count=len(self._lines),
                            cursor_position=Point(x=self.left_column, y=self.cursor_line), show_cursor=False)
        elapsed = perf_counter() - started
        self.content_calls += 1
        self.content_seconds += elapsed
        self.content_max_seconds = max(self.content_max_seconds, elapsed)
        return content

    def _resolve_position(self, *, partial=False):
        self.left_column = min(self.left_column, max(0, self._max_width - self._width))
        # Do not consume a saved anchor or follow-tail intent against an old
        # frame while its replacement is still in flight.
        if self._cache_key != self._requested_key and not partial:
            return
        if (self._cache_key != self._requested_key and self._pending_position is not None
                and self._pending_position.message_id not in self._anchor_starts):
            return
        if self._pending_position is not None and self.messages:
            position = self._pending_position
            starts = self._anchor_starts
            self.top_line = (starts[position.message_id] + position.offset
                             if position.message_id in starts else position.line)
            self.cursor_line = self.top_line
            self._pending_position = None
        self.cursor_line = min(self.cursor_line, len(self._lines) - 1)
        self.top_line = min(self.top_line, max(0, len(self._lines) - self._height))
        if self.follow_tail:
            self.cursor_line = len(self._lines) - 1
            self.top_line = max(0, len(self._lines) - self._height)
            self.follow_tail = self._cache_key != self._requested_key

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
            self.create_content(self._width, self._height)
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
        self.control._requested_key = None
        self.control._deferred_scroll = None
        if self.control.render_worker:
            self.control.render_worker.cancel(self.control.render_slot)
        self.control._lines = [[("", "")]]
        self.control._anchors = []
        self.control.objects = []
        self.control.restore_position(position)
