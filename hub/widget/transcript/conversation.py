"""Rich Markdown rendered into PTK cells, without printing to the terminal."""

from ..output.search_index import SearchKey, SearchHighlight, IndexedLines
from ..input_contract import ControlFragment

from .identity import TranscriptIdentity, TranscriptAnchor
from .render_contract import (
    RenderSection,
    TranscriptLayout,
    ToolCaption,
    TranscriptPresentation,
)

from hub.ui.output.worker import RenderSlot


from hub.model import ReasoningMessage

from hub.locales.keys import MessageKey

from hub.ui.output.model import OutputBlock

from llm.core.models import MessageStatus, MessageRole
from ...model import ChatMessage
from ...asset.icon import for_style
from bisect import bisect_right
import asyncio
from copy import copy
from time import perf_counter
from pathlib import Path
from prompt_toolkit.application.current import get_app
from ...ui.output import (
    OutputParser,
    OutputObject,
    RendererRegistry,
    ImageRenderer,
    RenderContext,
)
from ...ui.output.labels import block_title

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.layout import UIContent, Window
from prompt_toolkit.layout import ScrollOffsets
from prompt_toolkit.utils import get_cwidth
from rich.text import Text
from ...ui.output.markdown import HubMarkdown, make_console, _segment_style, _trim_right

from ...config.theme import HubTheme
from ...locales import Language
from ...config.profile import UserProfile
from ...config.view_state import ReadingPosition
from ..controls import ScrollbarMargin
from ..output.selection import SelectableControl
from ...config.theme_style import ThemeSelector, ThemeStyle
from ...ui.input.keys import KeyName
from ..feedback.interrupt_feedback import InterruptFeedback
from typing import NamedTuple


def render_messages(
    messages: tuple[ChatMessage, ...],
    width: int,
    theme: HubTheme,
    language=TranscriptLayout.ABSENT,
    *,
    anchors=TranscriptLayout.ABSENT,
    renderers=TranscriptLayout.ABSENT,
    root=TranscriptLayout.ABSENT,
    invalidate=TranscriptLayout.ABSENT,
    objects=TranscriptLayout.ABSENT,
    index_offset=TranscriptPresentation.ORIGIN,
    section=RenderSection.ALL,
    blocks=TranscriptLayout.ABSENT,
    render_block=TranscriptLayout.ABSENT
):
    """Return already-wrapped fragment lines using terminal-cell widths."""
    inner = TranscriptLayout.inner_width(width)
    t = language or Language(icons=for_style(theme.icon_style))
    console = make_console(inner, theme)
    lines = []
    account_name = UserProfile().display_name
    for local_index, message in enumerate(messages):
        if section == RenderSection.TOOLS:
            if not message.trace:
                lines.extend(render_tool_history(message.tools, width, theme, t))
            continue
        index = local_index + index_offset
        if (
            local_index
            and message.role == MessageRole.USER
            and messages[local_index - 1].role
            in (MessageRole.ASSISTANT, ReasoningMessage.ROLE)
        ):
            lines.extend([list(row) for row in TranscriptLayout.MESSAGE_GAP])
        if anchors is not TranscriptLayout.ABSENT and section != RenderSection.BODY:
            anchors.append(
                TranscriptAnchor(TranscriptIdentity.message(message, index), len(lines))
            )
        user = message.role == MessageRole.USER
        inset = (
            TranscriptPresentation.USER_INSET
            if inner >= TranscriptPresentation.MIN_INSET_WIDTH
            else TranscriptPresentation.NO_INSET
        )
        message_width = (
            max(
                TranscriptLayout.MIN_WIDTH,
                int(inner * TranscriptPresentation.USER_WIDTH_RATIO)
                - TranscriptPresentation.SIDES * inset,
            )
            if user
            else inner
        )
        label = (
            (message.author or account_name)
            if user
            else t(
                MessageKey.ASSISTANT
                if message.role == MessageRole.ASSISTANT
                else (
                    MessageKey.SYSTEM
                    if message.role == MessageRole.SYSTEM
                    else (
                        MessageKey.REASONING
                        if message.role == ReasoningMessage.ROLE
                        else MessageKey.INFO
                    )
                )
            )
        )
        if not user and message.author and message.role in (MessageRole.ASSISTANT, ReasoningMessage.ROLE):
            label = (message.author if message.role == MessageRole.ASSISTANT
                     else message.author + TranscriptPresentation.LABEL_SEPARATOR + t(MessageKey.REASONING))
        if message.role != MessageRole.SYSTEM and (
            message.activity or message.phase or message.status
        ):
            label += TranscriptPresentation.LABEL_SEPARATOR + (
                message.activity or t.status(message.phase or message.status)
            )
        if message.role == MessageRole.ASSISTANT:
            label += TranscriptPresentation.LABEL_SEPARATOR + t.elapsed(
                message.elapsed_seconds
            )
        elif message.time:
            label += (
                TranscriptPresentation.SYSTEM_TIME_SEPARATOR
                if message.role == MessageRole.SYSTEM
                else TranscriptPresentation.LABEL_SEPARATOR
            ) + message.time
        label = Text(label, overflow=TranscriptPresentation.OVERFLOW)
        header_lines = (
            []
            if message.role == ChatMessage.DRAFT or section == RenderSection.BODY
            else console.render_lines(
                label, console.options.update(width=inner), pad=TranscriptLayout.PAD
            )
        )
        for segments in header_lines:
            text = TranscriptLayout.EMPTY.join(segment.text for segment in segments)
            pad = (
                max(TranscriptPresentation.ORIGIN, inner - get_cwidth(text))
                if user
                else TranscriptPresentation.ORIGIN
            )
            lines.append(
                [
                    (
                        TranscriptLayout.EMPTY,
                        TranscriptLayout.INDENT + TranscriptLayout.SPACE * pad,
                    ),
                    (
                        (
                            ThemeStyle.joined(ThemeSelector.USER.style, ThemeStyle.BOLD)
                            if user
                            else (
                                ThemeStyle.joined(
                                    ThemeSelector.SYSTEM.style, ThemeStyle.BOLD
                                )
                                if message.role == MessageRole.SYSTEM
                                else ThemeStyle.joined(
                                    ThemeSelector.ACCENT.style, ThemeStyle.BOLD
                                )
                            )
                        ),
                        text,
                    ),
                ]
            )
        if section == RenderSection.HEADER:
            continue
        if message.trace:
            if section == RenderSection.ALL:
                from .thought_trace import render_trace_item

                for item in message.trace:
                    lines.extend(render_trace_item(item, width, theme, t))
            continue

        def markdown_lines(value):
            rendered = console.render_lines(
                value,
                console.options.update(width=message_width),
                pad=TranscriptLayout.PAD,
            )
            return [
                _trim_right(
                    [
                        (
                            _segment_style(segment.style, theme.code_background),
                            segment.text,
                        )
                        for segment in segments
                        if not segment.control
                    ]
                )
                for segments in rendered
            ]

        if (
            renderers is not TranscriptLayout.ABSENT
            and message.role == MessageRole.ASSISTANT
        ):
            parsed = (
                blocks
                if blocks is not TranscriptLayout.ABSENT
                else OutputParser().parse(
                    message.text,
                    final=message.status
                    not in (MessageStatus.STREAMING, MessageStatus.COMMITTED),
                )
            )
            context = RenderContext(
                message_width,
                theme,
                t,
                Path(root or Path.cwd()),
                invalidate or get_app().invalidate,
            )
            body = []
            for block in parsed:
                identifier = TranscriptIdentity.block(message, index, block.start)
                if anchors is not TranscriptLayout.ABSENT and message.id:
                    anchors.append(TranscriptAnchor(identifier, len(lines) + len(body)))
                rendered = (render_block or renderers.render)(block, context)
                if objects is not TranscriptLayout.ABSENT and renderers.is_object(
                    block
                ):
                    icons = for_style(theme.icon_style)
                    objects.append(
                        OutputObject(
                            identifier,
                            message.id,
                            block,
                            block_title(block),
                            tuple(tuple(row) for row in rendered),
                            len(lines) + len(body),
                            len(lines) + len(body) + len(rendered),
                            icons.for_output(block.kind),
                        )
                    )
                body.extend(rendered)
        elif not message.text and message.tools:
            body = []
        else:
            renderable = (
                HubMarkdown(message.text, theme)
                if message.role
                in (
                    MessageRole.ASSISTANT,
                    MessageRole.USER,
                    ChatMessage.DRAFT,
                    ReasoningMessage.ROLE,
                )
                else Text(message.text, overflow=TranscriptPresentation.OVERFLOW)
            )
            body = markdown_lines(renderable)
        if user:
            body_width = max(
                TranscriptLayout.MIN_WIDTH,
                max(
                    (sum(get_cwidth(text) for _, text in line) for line in body),
                    default=TranscriptPresentation.ORIGIN,
                ),
            )
            block_width = body_width + TranscriptPresentation.SIDES * inset
            padding = TranscriptLayout.INDENT + TranscriptLayout.SPACE * max(
                TranscriptPresentation.ORIGIN, inner - block_width
            )
            blank = [
                (TranscriptLayout.EMPTY, padding),
                (ThemeSelector.USER_BLOCK.style, TranscriptLayout.SPACE * block_width),
            ]
            lines.append(blank)
            for fragments in body:
                length = sum(get_cwidth(text) for _, text in fragments)
                lines.append(
                    [
                        (TranscriptLayout.EMPTY, padding),
                        (
                            ThemeSelector.USER_BLOCK.style,
                            TranscriptLayout.SPACE * inset,
                        ),
                        *(
                            (
                                ThemeStyle.joined(
                                    ThemeSelector.USER_BLOCK.style, style
                                ),
                                text,
                            )
                            for style, text in fragments
                        ),
                        (
                            ThemeSelector.USER_BLOCK.style,
                            TranscriptLayout.SPACE * (body_width - length + inset),
                        ),
                    ]
                )
            lines.append(blank)
        else:
            lines.extend(
                [(TranscriptLayout.EMPTY, TranscriptLayout.INDENT), *fragments]
                for fragments in body
            )
        lines.extend([list(row) for row in TranscriptLayout.MESSAGE_GAP])
        if section == RenderSection.ALL and message.tools:
            lines.extend(render_tool_history(message.tools, width, theme, t))
    return (
        lines
        if section in (RenderSection.HEADER, RenderSection.TOOLS)
        or any(m.trace for m in messages)
        else lines or [list(TranscriptLayout.BLANK_LINE)]
    )


def render_tool_history(steps, width, theme, language):
    """Only names and states; arguments and Tool payloads never enter Markdown."""
    if not steps:
        return []
    console = make_console(TranscriptLayout.inner_width(width), theme)
    text = [ToolCaption.render(step.name, step.status, language) for step in steps]
    rows = console.render_lines(
        Text(TranscriptLayout.NEWLINE.join(text)),
        console.options,
        pad=TranscriptLayout.PAD,
    )
    return [
        [
            (
                ThemeSelector.MUTED.style,
                TranscriptLayout.INDENT
                + TranscriptLayout.EMPTY.join(segment.text for segment in row),
            )
        ]
        for row in rows
    ] + [list(TranscriptLayout.BLANK_LINE)]


class TranscriptFrameKey(NamedTuple):
    width: int
    message_revision: int
    theme: object
    language: int
    root: str
    renderer_version: tuple
    loop: int

    def same_surface(self, requested):
        # Progressive frames may carry an earlier message revision on this surface.
        return self.width == requested.width and (self.renderer_version, self.loop) == (
            requested.renderer_version,
            requested.loop,
        )


class DeferredScroll(NamedTuple):
    base: str
    vertical: int
    horizontal: int


class ConversationControl(SelectableControl):
    ABSENT = None
    EMPTY = ""
    ORIGIN = 0
    MIN_DIMENSION = 1
    STOP_FOLLOWING = False
    NO_MATCH = -1
    NEXT_MATCH = 1
    PREVIOUS = -1
    NEXT = 1
    PAGE_OVERLAP = 2
    PARTIAL_FRAME = True
    COMPLETE_FRAME = False
    PROGRESSIVE = True
    SHOW_CURSOR = False
    FOCUSABLE = False
    NO_LOOP = 0
    INITIAL_COUNT = 0
    COUNT_STEP = 1
    INITIAL_SECONDS = 0.0
    RESIZE_DELAY = 0.075
    FALLBACK_SEPARATOR = "\n\n"
    MESSAGE_STORAGE = "_messages"
    MESSAGE_REVISION = "_message_revision"

    def __init__(
        self, messages: tuple[ChatMessage, ...], theme: HubTheme, language=ABSENT
    ) -> None:
        super().__init__()
        self.messages = messages
        self.theme = theme
        self.language = language or Language(
            icons=lambda: for_style(self.theme.icon_style)
        )
        self.file_root = Path.cwd()
        self.renderers = RendererRegistry()
        self.images = ImageRenderer()
        self.renderers.register(OutputBlock.HUB_IMAGE, self.images)
        self.cursor_line = ConversationControl.ORIGIN
        self.top_line = ConversationControl.ORIGIN
        self.left_column = ConversationControl.ORIGIN
        self._width = ConversationControl.MIN_DIMENSION
        self._height = ConversationControl.MIN_DIMENSION
        self.follow_tail = ConversationControl.STOP_FOLLOWING
        self._cache_key = ConversationControl.ABSENT
        from .message_cache import MessageRenderCache

        self._message_cache = MessageRenderCache()
        self._lines = [list(TranscriptLayout.BLANK_LINE)]
        self._anchors = []
        self._anchor_starts = {}
        self.objects = []
        self._pending_position = ConversationControl.ABSENT
        self.on_position_changed = ConversationControl.ABSENT
        self.search_query = ConversationControl.EMPTY
        self.search_index = ConversationControl.NO_MATCH
        self.search_hits = []
        self._search_lines = {}
        self._search_key = ConversationControl.ABSENT
        self.render_worker = ConversationControl.ABSENT
        self.render_slot = RenderSlot.TRANSCRIPT
        self._requested_key = ConversationControl.ABSENT
        self._resize_timer = ConversationControl.ABSENT
        self._max_width = ConversationControl.ORIGIN
        self.content_calls = ConversationControl.INITIAL_COUNT
        self.content_seconds = self.content_max_seconds = (
            ConversationControl.INITIAL_SECONDS
        )
        self._app = ConversationControl.ABSENT
        self._deferred_scroll = ConversationControl.ABSENT

    def _application(self):
        app = get_app()
        if app.is_running:
            self._app = app
        return (
            self._app
            if self._app is not ConversationControl.ABSENT and self._app.is_running
            else app
        )

    @property
    def messages(self):
        return self._messages

    @messages.setter
    def messages(self, value):
        if value is not getattr(
            self, ConversationControl.MESSAGE_STORAGE, ConversationControl.ABSENT
        ):
            self._messages = value
            self._urgent_messages = tuple(
                m
                for m in value
                if m.role == MessageRole.ASSISTANT
                and m.phase == InterruptFeedback.PHASE
            )
            self._message_revision = (
                getattr(
                    self,
                    ConversationControl.MESSAGE_REVISION,
                    ConversationControl.INITIAL_COUNT,
                )
                + ConversationControl.COUNT_STEP
            )

    def _request_frame(self, width, key):
        messages = self.messages
        theme, language, root = self.theme, self.language, self.file_root
        language = copy(language)
        language.icon_source = for_style(theme.icon_style)
        app = self._application()

        def build():
            return self._message_cache.prepare(
                messages,
                width,
                theme,
                language,
                render=render_messages,
                renderers=self.renderers,
                root=root,
                invalidate=app.invalidate,
            )

        def fallback(error):
            from .message_cache import Frame, frozen_rows
            from ...ui.output.registry import literal_lines

            rows = frozen_rows(
                literal_lines(
                    ConversationControl.FALLBACK_SEPARATOR.join(
                        m.text for m in messages
                    ),
                    max(ConversationControl.MIN_DIMENSION, width),
                )
            )
            return Frame(
                rows,
                (),
                (),
                max(map(fragment_list_width, rows), default=ConversationControl.ORIGIN),
            )

        def apply(frame):
            if (
                self._requested_key is ConversationControl.ABSENT
                or not key.same_surface(self._requested_key)
                or key.theme != self.theme
                or key.language != id(self.language)
                or key.root != str(self.file_root)
            ):
                return
            position = (
                self.reading_position()
                if self._cache_key is not ConversationControl.ABSENT
                else ConversationControl.ABSENT
            )
            self._lines, self._anchors, self.objects = (
                frame.lines,
                frame.anchors,
                frame.objects,
            )
            self._max_width = frame.max_width
            self._anchor_starts = frame.starts
            self._cache_key = key
            if (
                position is not ConversationControl.ABSENT
                and not self.follow_tail
                and not self.restoring
            ):
                self._pending_position = position
            self._resolve_position(partial=ConversationControl.PARTIAL_FRAME)
            if self._deferred_scroll is not ConversationControl.ABSENT:
                base, vertical, horizontal = self._deferred_scroll
                self._deferred_scroll = ConversationControl.ABSENT
                self._scroll_to(
                    (
                        len(self._lines) - self._height
                        if base == KeyName.END
                        else ConversationControl.ORIGIN
                    )
                    + vertical
                )
                self.left_column = max(
                    ConversationControl.ORIGIN,
                    min(
                        max(ConversationControl.ORIGIN, self._max_width - self._width),
                        horizontal,
                    ),
                )
            app.invalidate()

        if self.render_worker is not ConversationControl.ABSENT and app.is_running:
            self.render_worker.request(
                self.render_slot,
                build,
                apply,
                fallback,
                progressive=ConversationControl.PROGRESSIVE,
            )
        else:
            apply(build())

    def _index_search(self) -> None:
        key = SearchKey(self._cache_key, self.search_query)
        if key == self._search_key:
            return
        self._search_key = key
        self.search_hits = []
        self._search_lines = {}
        if not self.search_query:
            if self.render_worker:
                self.render_worker.cancel(RenderSlot.SEARCH)
            return
        from ..output.search_index import index_lines

        lines, query = self._lines, self.search_query
        app = self._application()

        def apply(result):
            if self._search_key != key or self._cache_key != key.source:
                return
            self.search_hits, self._search_lines = result
            self.search_index = min(self.search_index, len(self.search_hits) - 1)
            if self.search_index == ConversationControl.NO_MATCH and self.search_hits:
                self.move_search()
            app.invalidate()

        if self.render_worker and app.is_running:
            self.render_worker.request(
                RenderSlot.SEARCH,
                lambda: index_lines(lines, query),
                apply,
                lambda _: IndexedLines.empty(),
            )
        else:
            self.search_hits, self._search_lines = index_lines(lines, query)
            self.search_index = min(self.search_index, len(self.search_hits) - 1)

    def set_search(self, query: str) -> None:
        self.search_query = query
        self.search_index = ConversationControl.NO_MATCH
        self._index_search()
        if self.search_hits:
            self.move_search()

    def move_search(self, direction: int = NEXT_MATCH) -> None:
        self._index_search()
        if not self.search_hits:
            return
        self.search_index = (self.search_index + direction) % len(self.search_hits)
        row, start, _ = self.search_hits[self.search_index]
        self._scroll_to(row)
        text = ConversationControl.EMPTY.join(
            ControlFragment.text(fragment) for fragment in self._lines[row]
        )
        column = get_cwidth(text[:start])
        self.left_column = (
            max(ConversationControl.ORIGIN, column - self._width + 1)
            if column >= self._width
            else ConversationControl.ORIGIN
        )

    def _search_line(self, row):
        hits = self._search_lines.get(row)
        if not hits:
            return self._lines[row]
        return SearchHighlight.apply(self._lines[row], hits, lambda: self.search_index)

    def scroll(self, key: str) -> None:
        # A snapshot can arrive between paints. Apply navigation to its current
        # layout, not the previous session's empty or differently wrapped rows.
        self.create_content(self._width, self._height)
        if self._cache_key is ConversationControl.ABSENT or (
            not self._anchors and self._cache_key != self._requested_key
        ):
            # A painted empty frame has a cache key too. While the first real
            # frame is pending, do not clamp navigation to that empty viewport.
            base, vertical, horizontal = self._deferred_scroll or DeferredScroll(
                KeyName.HOME, ConversationControl.ORIGIN, ConversationControl.ORIGIN
            )
            if key in (KeyName.HOME, KeyName.END):
                base, vertical = key, ConversationControl.ORIGIN
            elif key in (KeyName.LEFT, KeyName.RIGHT):
                horizontal += (
                    ConversationControl.PREVIOUS
                    if key == KeyName.LEFT
                    else ConversationControl.NEXT
                )
            else:
                page = max(
                    ConversationControl.MIN_DIMENSION,
                    self._height - ConversationControl.PAGE_OVERLAP,
                )
                vertical += {
                    KeyName.UP: ConversationControl.PREVIOUS,
                    KeyName.DOWN: ConversationControl.NEXT,
                    KeyName.PAGE_UP: -page,
                    KeyName.PAGE_DOWN: page,
                }[key]
            self._deferred_scroll = DeferredScroll(base, vertical, horizontal)
            self._pending_position = ConversationControl.ABSENT
            self.follow_tail = ConversationControl.STOP_FOLLOWING
            return
        if key in (KeyName.LEFT, KeyName.RIGHT):
            limit = max(ConversationControl.ORIGIN, self._max_width - self._width)
            self.left_column = max(
                ConversationControl.ORIGIN,
                min(
                    limit,
                    self.left_column
                    + (
                        ConversationControl.PREVIOUS
                        if key == KeyName.LEFT
                        else ConversationControl.NEXT
                    ),
                ),
            )
        elif key == KeyName.HOME:
            self._scroll_to(ConversationControl.ORIGIN)
        elif key == KeyName.END:
            self._scroll_to(len(self._lines))
            self.cursor_line = len(self._lines) - 1
            self.follow_tail = self._cache_key != self._requested_key
        else:
            page = max(
                ConversationControl.MIN_DIMENSION,
                self._height - ConversationControl.PAGE_OVERLAP,
            )
            delta = {
                KeyName.UP: ConversationControl.PREVIOUS,
                KeyName.DOWN: ConversationControl.NEXT,
                KeyName.PAGE_UP: -page,
                KeyName.PAGE_DOWN: page,
            }[key]
            self._scroll_to(self.top_line + delta)

    def _scroll_to(self, line):
        self._pending_position = ConversationControl.ABSENT
        self.follow_tail = ConversationControl.STOP_FOLLOWING
        self.top_line = max(
            ConversationControl.ORIGIN,
            min(max(ConversationControl.ORIGIN, len(self._lines) - self._height), line),
        )
        self.cursor_line = self.top_line
        if self.on_position_changed:
            self.on_position_changed()

    def scroll_to(self, line):
        self._scroll_to(line)

    @property
    def restoring(self):
        return self._pending_position is not ConversationControl.ABSENT

    def anchor_positions(self):
        """Return the current presentation anchor map as an independent projection."""
        return dict(self._anchors)

    def reading_position(self):
        if self._pending_position is not ConversationControl.ABSENT:
            return self._pending_position
        index = (
            bisect_right(self._anchors, self.top_line, key=TranscriptAnchor.row_of) - 1
        )
        message_id, start = (
            self._anchors[index]
            if index >= 0
            else (ConversationControl.EMPTY, ConversationControl.ORIGIN)
        )
        return ReadingPosition(message_id, self.top_line - start, self.top_line)

    def restore_position(self, position):
        self._pending_position = position
        self.follow_tail = ConversationControl.STOP_FOLLOWING

    def is_focusable(self) -> bool:
        return ConversationControl.FOCUSABLE

    def _urgent_headers(self):
        """Paint local stop acknowledgement independently of the Markdown lane."""
        headers, extra = {}, []
        for message in self._urgent_messages:
            rows = render_messages(
                (message,),
                self._width,
                self.theme,
                self.language,
                section=RenderSection.HEADER,
            )
            start = self._anchor_starts.get(message.id)
            if start is ConversationControl.ABSENT:
                extra.extend(rows)
            else:
                # Keep the existing body and row geometry intact. The durable
                # frame handles reflow; immediate feedback uses its first row.
                headers[start] = rows[0]
        return headers, extra

    def create_content(self, width: int, height: int) -> UIContent:
        started = perf_counter()
        self._height = max(ConversationControl.MIN_DIMENSION, height)
        self._width = max(ConversationControl.MIN_DIMENSION, width)
        app = self._application()
        loop_id = (
            id(asyncio.get_running_loop())
            if app.is_running
            else ConversationControl.NO_LOOP
        )
        key = TranscriptFrameKey(
            width,
            self._message_revision,
            self.theme,
            id(self.language),
            str(self.file_root),
            self.renderers.version,
            loop_id,
        )
        if self._requested_key != key:
            resized = (
                self._requested_key is not ConversationControl.ABSENT
                and self._requested_key.width != width
            )
            self._requested_key = key
            if self._resize_timer and resized:
                self._resize_timer.cancel()
                self._resize_timer = ConversationControl.ABSENT
            if (
                resized
                and self.render_worker is not ConversationControl.ABSENT
                and app.is_running
            ):
                self.render_worker.cancel(self.render_slot)

                def resized_frame():
                    self._resize_timer = ConversationControl.ABSENT
                    self._request_frame(self._width, self._requested_key)
                    app.invalidate()

                self._resize_timer = asyncio.get_running_loop().call_later(
                    ConversationControl.RESIZE_DELAY, resized_frame
                )
            elif self._resize_timer is ConversationControl.ABSENT:
                self._request_frame(width, key)
        self._resolve_position()
        self._index_search()
        urgent, extra = self._urgent_headers()

        def get_line(row):
            if row >= len(self._lines):
                return extra[row - len(self._lines)]
            return urgent[row] if row in urgent else self._search_line(row)

        content = UIContent(
            get_line=get_line,
            line_count=len(self._lines) + len(extra),
            cursor_position=Point(x=self.left_column, y=self.cursor_line),
            show_cursor=ConversationControl.SHOW_CURSOR,
        )
        elapsed = perf_counter() - started
        self.content_calls += ConversationControl.COUNT_STEP
        self.content_seconds += elapsed
        self.content_max_seconds = max(self.content_max_seconds, elapsed)
        return self.select_content(content, width)

    def _resolve_position(self, *, partial=COMPLETE_FRAME):
        self.left_column = min(
            self.left_column,
            max(ConversationControl.ORIGIN, self._max_width - self._width),
        )
        # Do not consume a saved anchor or follow-tail intent against an old
        # frame while its replacement is still in flight.
        if self._cache_key != self._requested_key and not partial:
            return
        if (
            self._cache_key != self._requested_key
            and self._pending_position is not ConversationControl.ABSENT
            and self._pending_position.message_id not in self._anchor_starts
        ):
            return
        if self._pending_position is not ConversationControl.ABSENT and self.messages:
            position = self._pending_position
            starts = self._anchor_starts
            self.top_line = (
                starts[position.message_id] + position.offset
                if position.message_id in starts
                else position.line
            )
            self.cursor_line = self.top_line
            self._pending_position = ConversationControl.ABSENT
        self.cursor_line = min(self.cursor_line, len(self._lines) - 1)
        self.top_line = min(
            self.top_line,
            max(ConversationControl.ORIGIN, len(self._lines) - self._height),
        )
        if self.follow_tail:
            self.cursor_line = len(self._lines) - 1
            self.top_line = max(
                ConversationControl.ORIGIN, len(self._lines) - self._height
            )
            self.follow_tail = self._cache_key != self._requested_key

    def replace_messages(self, messages, position, reset_scroll):
        # A debounced resize belongs to the old transcript too. It must not
        # enqueue a frame after the replacement clears its requested key.
        if self._resize_timer:
            self._resize_timer.cancel()
            self._resize_timer = ConversationControl.ABSENT
        self.selection.clear()
        self.images.reset()
        self.set_search(ConversationControl.EMPTY)
        self.messages = messages
        self.cursor_line = ConversationControl.ORIGIN
        self.top_line = ConversationControl.ORIGIN
        self.left_column = ConversationControl.ORIGIN
        reset_scroll()
        self._cache_key = ConversationControl.ABSENT
        self._requested_key = ConversationControl.ABSENT
        self._deferred_scroll = ConversationControl.ABSENT
        if self.render_worker:
            self.render_worker.cancel(self.render_slot)
        self._lines = [list(TranscriptLayout.BLANK_LINE)]
        self._anchors = []
        self._anchor_starts = {}
        self.objects = []
        self.restore_position(position)


class ConversationView:
    ABSENT = None
    ORIGIN = 0
    WRAP = False
    SCROLL_OFFSET = 0
    ARROWS = True

    def __init__(
        self, messages: tuple[ChatMessage, ...], theme: HubTheme, language=ABSENT
    ) -> None:
        self.control = ConversationControl(messages, theme, language)
        self.window = Window(
            self.control,
            style=ThemeSelector.ROOT.style,
            wrap_lines=ConversationView.WRAP,
            get_vertical_scroll=lambda window: self.control.top_line,
            get_horizontal_scroll=lambda window: self.control.left_column,
            scroll_offsets=ScrollOffsets(
                top=ConversationView.SCROLL_OFFSET,
                bottom=ConversationView.SCROLL_OFFSET,
            ),
            right_margins=[ScrollbarMargin(display_arrows=ConversationView.ARROWS)],
        )

    def __pt_container__(self):
        return self.window

    def set_messages(self, messages: tuple[ChatMessage, ...], position=ABSENT) -> None:
        def reset_scroll():
            self.window.vertical_scroll = ConversationView.ORIGIN

        self.control.replace_messages(messages, position, reset_scroll)
