"""Explicit renderer registration; tags never name importable Python classes."""

from hub.locales.keys import MessageKey

import re
from typing import Protocol

from prompt_toolkit.utils import get_cwidth

from .model import FragmentLines, OutputBlock, RenderContext
from .markdown import MarkdownRenderer


def literal_lines(text: str, width: int, style: str = "") -> FragmentLines:
    lines = []
    for source in text.splitlines() or [""]:
        row, used = "", 0
        for char in source:
            char = char if char.isprintable() else " "
            size = get_cwidth(char)
            if row and used + size > max(1, width):
                lines.append([(style, row)])
                row, used = "", 0
            row += char
            used += size
        lines.append([(style, row)])
    return lines


class OutputRenderer(Protocol):
    def render(self, block: OutputBlock, context: RenderContext) -> FragmentLines: ...


class RendererRegistry:
    def __init__(self):
        self._renderers: dict[str, OutputRenderer] = {name: MarkdownRenderer() for name in (OutputBlock.MARKDOWN, OutputBlock.CODE, OutputBlock.TABLE)}
        from .structured import StructuredRenderer
        self._renderers.update({name: StructuredRenderer() for name in
                               (OutputBlock.HUB_CODE, OutputBlock.HUB_DIFF, OutputBlock.HUB_TABLE, OutputBlock.HUB_CARD, OutputBlock.HUB_CHART)})
        self._version = 0
        self._interactions = {}
        self.on_close = None

    @property
    def structure_version(self):
        return self._version

    def block_revision(self, block, context):
        renderer = self._renderers.get(block.kind)
        revision = getattr(renderer, "block_revision", None)
        return revision(block, context) if revision else getattr(renderer, "revision", 0)

    @property
    def version(self):
        return self._version, tuple(getattr(renderer, "revision", 0) for renderer in self._renderers.values())

    def register(self, tag: str, renderer: OutputRenderer, *, interaction=None) -> None:
        if not re.fullmatch(OutputBlock.TAG_PATTERN, tag):
            raise ValueError("Output tags must use the hub- prefix")
        if tag in self._renderers:
            raise ValueError(f"Renderer already registered: {tag}")
        self._renderers[tag] = renderer
        if interaction is not None:
            self._interactions[tag] = interaction
        self._version += 1

    def create_interaction(self, item, context):
        """Create a fresh expanded UI without mutating the transcript object."""
        from ...widget.output_inspector import OutputInspector
        from ...widget.output.table_inspector import TableInspector
        from ...widget.output.code_viewer import CodeViewer
        factory = self._interactions.get(item.block.kind)
        if factory is None:
            factory = (TableInspector if item.block.kind in (OutputBlock.TABLE, OutputBlock.HUB_TABLE) else
                       CodeViewer if item.block.kind in (OutputBlock.CODE, OutputBlock.HUB_CODE) else OutputInspector)
        try:
            return factory(item, context)
        except Exception as error:
            # An extension failure must leave the source accessible and closable.
            return OutputInspector(item, context, error=str(error))

    def is_object(self, block):
        return block.kind != OutputBlock.MARKDOWN and block.kind in self._renderers

    def render(self, block: OutputBlock, context: RenderContext) -> FragmentLines:
        renderer = self._renderers.get(block.kind)
        if renderer is None:
            text = context.language(MessageKey.OUTPUT_PENDING) if block.kind == OutputBlock.PENDING else block.raw or block.text
            return literal_lines(text, context.width, "class:hub.muted")
        try:
            return renderer.render(block, context)
        except Exception as error:
            return literal_lines(context.language(MessageKey.OUTPUT_ERROR, error=str(error)), context.width, "class:hub.notice")

    def close(self):
        if self.on_close:
            self.on_close()
        for renderer in self._renderers.values():
            close = getattr(renderer, "close", None)
            if close:
                close()
