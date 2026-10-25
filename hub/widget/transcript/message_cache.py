"""Worker-owned caches and immutable prefix/tail display frames."""
from .identity import TranscriptIdentity


from hub.model import ReasoningMessage

from llm.core.models import MessageStatus
from bisect import bisect_right
from collections.abc import Sequence
from collections import ChainMap
from dataclasses import dataclass, replace
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
    lines: Sequence
    anchors: Sequence
    objects: Sequence
    max_width: int
    starts: object = None

    def __post_init__(self):
        if self.starts is None:
            object.__setattr__(self, "starts", MappingProxyType(dict(self.anchors)))


def frozen_rows(rows):
    return tuple(tuple(row) for row in rows)


class MessageRenderCache:
    def __init__(self):
        self.context = None
        self.items = {}
        self.parser = OutputParser()
        self.body_renders = self.header_renders = self.prefix_builds = self.tool_renders = 0
        self.trace_renders = 0
        self._prefix_key = None
        self._prefix = None

    def prepare(self, messages, width, theme, language, *, render, renderers, root, invalidate):
        context = (width, theme, id(language.messages), language.code, str(root), renderers.structure_version)
        if context != self.context:
            self.items.clear()
            self._prefix_key = None
            self.context = context
        if not messages:
            self.items.clear()
            self._prefix_key = None
            rows = frozen_rows(render((), width, theme, language))
            return Frame(rows, (), (), max(map(fragment_list_width, rows), default=0))
        entries, retained = [], {}
        for index, message in enumerate(messages):
            identifier = TranscriptIdentity.message(message, index)
            previous = self.items.get(identifier, {})
            body_key = (message.role, "" if message.trace else message.text,
                        message.status not in (MessageStatus.STREAMING, MessageStatus.COMMITTED), index if not message.id else None, bool(message.trace))
            blocks = previous.get("blocks", ())
            if previous.get("body_key") != body_key:
                blocks = self.parser.parse(message.text, final=body_key[2]) if message.role == "assistant" else ()
            render_context = RenderContext(max(1, width - 4), theme, language, Path(root), invalidate)
            revisions = tuple(renderers.block_revision(block, render_context) for block in blocks)
            body = previous.get("body")
            block_cache = previous.get("block_cache", {})
            if body is None or previous.get("body_key") != body_key or previous.get("revisions") != revisions:
                used_blocks = {}
                def render_block(block, ctx):
                    key = (block, renderers.block_revision(block, ctx))
                    rows = block_cache.get(key)
                    if rows is None:
                        rows = frozen_rows(renderers.render(block, ctx))
                    used_blocks[key] = rows
                    return rows
                anchors, objects = [], []
                lines = frozen_rows(render((message,), width, theme, language, section="body",
                    blocks=blocks, render_block=render_block, anchors=anchors, objects=objects,
                    renderers=renderers, root=root, invalidate=invalidate, index_offset=index))
                body = Frame(lines, tuple(anchors), tuple(objects), max(map(fragment_list_width, lines), default=0))
                block_cache = used_blocks
                self.body_renders += 1
            header_key = (message.role, message.author, message.status, message.phase, message.time, message.activity,
                          max(0, int(message.elapsed_seconds)) if message.elapsed_seconds is not None else None)
            header = previous.get("header")
            if header is None or previous.get("header_key") != header_key:
                header = frozen_rows(render((message,), width, theme, language, section="header"))
                self.header_renders += 1
            tools_key = () if message.trace else message.tools
            tools = previous.get("tools")
            if tools is None or previous.get("tools_key") != tools_key:
                tools = frozen_rows(render((message,), width, theme, language, section="tools")) if tools_key else ()
                self.tool_renders += bool(message.tools)
            trace_cache, trace_parts, trace_width = {}, [], 0
            from .thought_trace import render_trace_item
            for item in message.trace:
                cached = previous.get("trace_cache", {}).get(item)
                if cached is None:
                    rows = render_trace_item(item, width, theme, language)
                    cached = rows, max(map(fragment_list_width, rows), default=0)
                    self.trace_renders += 1
                trace_cache[item] = cached
                trace_parts.append(cached[0])
                trace_width = max(trace_width, cached[1])
            trace = previous.get("trace") if previous.get("trace_key") == message.trace else Joined(*trace_parts)
            if (previous.get("body") is body and previous.get("header") is header
                    and previous.get("tools") is tools and previous.get("trace") is trace):
                frame = previous["frame"]
            else:
                offset = len(header)
                frame = Frame(Joined(header, body.lines, tools, trace or ()), ((identifier, 0), *((name, row + offset) for name, row in body.anchors)),
                    tuple(replace(obj, start_line=obj.start_line + offset, end_line=obj.end_line + offset) for obj in body.objects),
                    max(body.max_width, max(map(fragment_list_width, header), default=0),
                        max(map(fragment_list_width, tools), default=0), trace_width))
            retained[identifier] = dict(body_key=body_key, blocks=blocks, revisions=revisions, body=body,
                block_cache=block_cache, header_key=header_key, header=header, tools=tools,
                tools_key=tools_key, trace_key=message.trace, trace=trace, trace_cache=trace_cache, frame=frame)
            entries.append(frame)
        self.items = retained
        # Queued user messages can follow the currently streaming response.
        # Keep that whole changing region out of the historical prefix too.
        tail_start = next((index for index, message in enumerate(messages)
                           if message.role == ReasoningMessage.ROLE or message.role == "assistant"
                           and message.status in (MessageStatus.STREAMING, MessageStatus.COMMITTED)), len(entries) - 1)
        prefix_key = tuple(id(entry) for entry in entries[:tail_start])
        if prefix_key != self._prefix_key:
            self._prefix = self._assemble(entries[:tail_start], messages, 0)
            self._prefix_key = prefix_key
            self.prefix_builds += 1
        tail = self._assemble(entries[tail_start:], messages, tail_start, len(self._prefix.lines))
        return Frame(Joined(self._prefix.lines, tail.lines), Joined(self._prefix.anchors, tail.anchors),
                     Joined(self._prefix.objects, tail.objects), max(self._prefix.max_width, tail.max_width),
                     ChainMap(tail.starts, self._prefix.starts))

    @staticmethod
    def _assemble(entries, messages, start, offset=0):
        parts, anchors, objects = [], [], []
        maximum = 0
        for index, frame in enumerate(entries, start):
            if index and messages[index].role == "user" and messages[index - 1].role in ("assistant", ReasoningMessage.ROLE):
                parts.append(((('', ''),), (('', ''),)))
                offset += 2
            parts.append(frame.lines)
            anchors.extend((name, row + offset) for name, row in frame.anchors)
            objects.extend(replace(obj, start_line=obj.start_line + offset, end_line=obj.end_line + offset) for obj in frame.objects)
            offset += len(frame.lines)
            maximum = max(maximum, frame.max_width)
        return Frame(Joined(*parts), tuple(anchors), tuple(objects), maximum)

    def render(self, *args, **kwargs):
        frame = self.prepare(*args, **kwargs)
        return frame.lines, frame.anchors, frame.objects
