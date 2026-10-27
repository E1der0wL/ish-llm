"""Worker-owned caches and immutable prefix/tail display frames."""

from .identity import TranscriptIdentity, TranscriptAnchor


from hub.model import ReasoningMessage

from llm.core.models import MessageStatus, MessageRole
from .render_contract import RenderSection, TranscriptLayout
from bisect import bisect_right
from collections.abc import Sequence
from collections import ChainMap
from dataclasses import dataclass, replace, field
from typing import NamedTuple
from pathlib import Path
from types import MappingProxyType
from prompt_toolkit.formatted_text.utils import fragment_list_width
from ...ui.output import OutputParser, RenderContext


class Joined(Sequence):
    """Read-only concatenation; appending a tail never copies prefix rows."""

    def __init__(self, *parts):
        self.parts = tuple(part for part in parts if len(part))
        ends, total = [], 0
        for part in self.parts:
            total += len(part)
            ends.append(total)
        self.ends = tuple(ends)

    def __len__(self):
        return self.ends[-1] if self.ends else 0

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self)[index]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        part = bisect_right(self.ends, index)
        return self.parts[part][index - (self.ends[part - 1] if part else 0)]

    def __iter__(self):
        for part in self.parts:
            yield from part

    def __eq__(self, other):
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))


@dataclass(frozen=True, slots=True)
class Frame:
    ABSENT = None
    lines: Sequence
    anchors: Sequence
    objects: Sequence
    max_width: int
    starts: object = None

    def __post_init__(self):
        if self.starts is Frame.ABSENT:
            object.__setattr__(
                self, Frame.starts.__name__, MappingProxyType(dict(self.anchors))
            )


def frozen_rows(rows):
    return tuple(tuple(row) for row in rows)


class CacheContext(NamedTuple):
    width: int
    theme: object
    translations: int
    language: str
    root: str
    renderer_structure: int


class BodyKey(NamedTuple):
    role: str
    text: str
    final: bool
    index: object
    traced: bool


class HeaderKey(NamedTuple):
    role: str
    author: str
    status: str
    phase: str
    time: str
    activity: str
    elapsed: object


class TraceRows(NamedTuple):
    lines: tuple
    width: int


@dataclass(frozen=True, slots=True)
class CachedMessage:
    body_key: object = None
    blocks: tuple = ()
    revisions: object = None
    body: object = None
    block_cache: dict = field(default_factory=dict)
    header_key: object = None
    header: object = None
    tools: object = None
    tools_key: object = None
    trace_key: object = None
    trace: object = None
    trace_cache: dict = field(default_factory=dict)
    frame: object = None


class MessageRenderCache:
    ABSENT = None
    EMPTY = ""
    INITIAL_COUNT = 0
    COUNT_STEP = 1
    NO_WIDTH = 0
    ORIGIN = 0
    MIN_ELAPSED = 0

    def __init__(self):
        self.context = MessageRenderCache.ABSENT
        self.items = {}
        self.parser = OutputParser()
        self.body_renders = self.header_renders = self.prefix_builds = (
            self.tool_renders
        ) = MessageRenderCache.INITIAL_COUNT
        self.trace_renders = MessageRenderCache.INITIAL_COUNT
        self._prefix_key = MessageRenderCache.ABSENT
        self._prefix = MessageRenderCache.ABSENT

    def prepare(
        self, messages, width, theme, language, *, render, renderers, root, invalidate
    ):
        context = CacheContext(
            width,
            theme,
            id(language.messages),
            language.code,
            str(root),
            renderers.structure_version,
        )
        if context != self.context:
            self.items.clear()
            self._prefix_key = MessageRenderCache.ABSENT
            self.context = context
        if not messages:
            self.items.clear()
            self._prefix_key = MessageRenderCache.ABSENT
            rows = frozen_rows(render((), width, theme, language))
            return Frame(
                rows,
                (),
                (),
                max(
                    map(fragment_list_width, rows), default=MessageRenderCache.NO_WIDTH
                ),
            )
        entries, retained = [], {}
        for index, message in enumerate(messages):
            identifier = TranscriptIdentity.message(message, index)
            previous = self.items.get(identifier)
            if previous is MessageRenderCache.ABSENT:
                previous = CachedMessage()
            body_key = BodyKey(
                message.role,
                MessageRenderCache.EMPTY if message.trace else message.text,
                message.status
                not in (MessageStatus.STREAMING, MessageStatus.COMMITTED),
                index if not message.id else MessageRenderCache.ABSENT,
                bool(message.trace),
            )
            blocks = previous.blocks
            if previous.body_key != body_key:
                blocks = (
                    self.parser.parse(message.text, final=body_key.final)
                    if message.role == MessageRole.ASSISTANT
                    else ()
                )
            render_context = RenderContext(
                TranscriptLayout.inner_width(width),
                theme,
                language,
                Path(root),
                invalidate,
            )
            revisions = tuple(
                renderers.block_revision(block, render_context) for block in blocks
            )
            body = previous.body
            block_cache = previous.block_cache
            if (
                body is MessageRenderCache.ABSENT
                or previous.body_key != body_key
                or previous.revisions != revisions
            ):
                used_blocks = {}

                def render_block(block, ctx):
                    key = (block, renderers.block_revision(block, ctx))
                    rows = block_cache.get(key)
                    if rows is MessageRenderCache.ABSENT:
                        rows = frozen_rows(renderers.render(block, ctx))
                    used_blocks[key] = rows
                    return rows

                anchors, objects = [], []
                lines = frozen_rows(
                    render(
                        (message,),
                        width,
                        theme,
                        language,
                        section=RenderSection.BODY,
                        blocks=blocks,
                        render_block=render_block,
                        anchors=anchors,
                        objects=objects,
                        renderers=renderers,
                        root=root,
                        invalidate=invalidate,
                        index_offset=index,
                    )
                )
                body = Frame(
                    lines,
                    tuple(anchors),
                    tuple(objects),
                    max(
                        map(fragment_list_width, lines),
                        default=MessageRenderCache.NO_WIDTH,
                    ),
                )
                block_cache = used_blocks
                self.body_renders += MessageRenderCache.COUNT_STEP
            header_key = HeaderKey(
                message.role,
                message.author,
                message.status,
                message.phase,
                message.time,
                message.activity,
                (
                    max(MessageRenderCache.MIN_ELAPSED, int(message.elapsed_seconds))
                    if message.elapsed_seconds is not MessageRenderCache.ABSENT
                    else MessageRenderCache.ABSENT
                ),
            )
            header = previous.header
            if header is MessageRenderCache.ABSENT or previous.header_key != header_key:
                header = frozen_rows(
                    render(
                        (message,), width, theme, language, section=RenderSection.HEADER
                    )
                )
                self.header_renders += MessageRenderCache.COUNT_STEP
            tools_key = () if message.trace else message.tools
            tools = previous.tools
            if tools is MessageRenderCache.ABSENT or previous.tools_key != tools_key:
                tools = (
                    frozen_rows(
                        render(
                            (message,),
                            width,
                            theme,
                            language,
                            section=RenderSection.TOOLS,
                        )
                    )
                    if tools_key
                    else ()
                )
                self.tool_renders += bool(message.tools)
            trace_cache, trace_parts, trace_width = {}, [], MessageRenderCache.NO_WIDTH
            from .thought_trace import render_trace_item

            for item in message.trace:
                cached = previous.trace_cache.get(item)
                if cached is MessageRenderCache.ABSENT:
                    rows = render_trace_item(item, width, theme, language)
                    cached = TraceRows(
                        rows,
                        max(
                            map(fragment_list_width, rows),
                            default=MessageRenderCache.NO_WIDTH,
                        ),
                    )
                    self.trace_renders += MessageRenderCache.COUNT_STEP
                trace_cache[item] = cached
                trace_parts.append(cached.lines)
                trace_width = max(trace_width, cached.width)
            trace = (
                previous.trace
                if previous.trace_key == message.trace
                else Joined(*trace_parts)
            )
            if (
                previous.body is body
                and previous.header is header
                and previous.tools is tools
                and previous.trace is trace
            ):
                frame = previous.frame
            else:
                offset = len(header)
                frame = Frame(
                    Joined(header, body.lines, tools, trace or ()),
                    (
                        TranscriptAnchor(identifier, MessageRenderCache.ORIGIN),
                        *(TranscriptAnchor(name, row + offset) for name, row in body.anchors),
                    ),
                    tuple(
                        replace(
                            obj,
                            start_line=obj.start_line + offset,
                            end_line=obj.end_line + offset,
                        )
                        for obj in body.objects
                    ),
                    max(
                        body.max_width,
                        max(
                            map(fragment_list_width, header),
                            default=MessageRenderCache.NO_WIDTH,
                        ),
                        max(
                            map(fragment_list_width, tools),
                            default=MessageRenderCache.NO_WIDTH,
                        ),
                        trace_width,
                    ),
                )
            retained[identifier] = CachedMessage(
                body_key=body_key,
                blocks=blocks,
                revisions=revisions,
                body=body,
                block_cache=block_cache,
                header_key=header_key,
                header=header,
                tools=tools,
                tools_key=tools_key,
                trace_key=message.trace,
                trace=trace,
                trace_cache=trace_cache,
                frame=frame,
            )
            entries.append(frame)
        self.items = retained
        # Queued user messages can follow the currently streaming response.
        # Keep that whole changing region out of the historical prefix too.
        active = {message.id for message in messages
                  if message.role == MessageRole.ASSISTANT
                  and message.status in (MessageStatus.STREAMING, MessageStatus.COMMITTED)}
        tail_start = next(
            (
                index
                for index, message in enumerate(messages)
                if (message.role == ReasoningMessage.ROLE
                    and ReasoningMessage.assistant_id(message.id) in active)
                or message.role == MessageRole.ASSISTANT
                and message.status in (MessageStatus.STREAMING, MessageStatus.COMMITTED)
            ),
            len(entries) - 1,
        )
        prefix_key = tuple(id(entry) for entry in entries[:tail_start])
        if prefix_key != self._prefix_key:
            self._prefix = self._assemble(
                entries[:tail_start], messages, MessageRenderCache.ORIGIN
            )
            self._prefix_key = prefix_key
            self.prefix_builds += MessageRenderCache.COUNT_STEP
        tail = self._assemble(
            entries[tail_start:], messages, tail_start, len(self._prefix.lines)
        )
        return Frame(
            Joined(self._prefix.lines, tail.lines),
            Joined(self._prefix.anchors, tail.anchors),
            Joined(self._prefix.objects, tail.objects),
            max(self._prefix.max_width, tail.max_width),
            ChainMap(tail.starts, self._prefix.starts),
        )

    @staticmethod
    def _assemble(entries, messages, start, offset=ORIGIN):
        parts, anchors, objects = [], [], []
        maximum = MessageRenderCache.NO_WIDTH
        for index, frame in enumerate(entries, start):
            if (
                index
                and messages[index].role == MessageRole.USER
                and messages[index - 1].role
                in (MessageRole.ASSISTANT, ReasoningMessage.ROLE)
            ):
                parts.append(TranscriptLayout.MESSAGE_GAP)
                offset += len(TranscriptLayout.MESSAGE_GAP)
            parts.append(frame.lines)
            anchors.extend(TranscriptAnchor(name, row + offset) for name, row in frame.anchors)
            objects.extend(
                replace(
                    obj,
                    start_line=obj.start_line + offset,
                    end_line=obj.end_line + offset,
                )
                for obj in frame.objects
            )
            offset += len(frame.lines)
            maximum = max(maximum, frame.max_width)
        return Frame(Joined(*parts), tuple(anchors), tuple(objects), maximum)

    def render(self, *args, **kwargs):
        frame = self.prepare(*args, **kwargs)
        return frame.lines, frame.anchors, frame.objects
