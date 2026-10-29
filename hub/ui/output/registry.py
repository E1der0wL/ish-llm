"""Explicit renderer registration; tags never name importable Python classes."""

from hub.locales.keys import MessageKey

import re
from typing import Protocol

from prompt_toolkit.utils import get_cwidth

from .model import FragmentLines, OutputBlock, RenderContext
from .markdown import MarkdownRenderer
from ...config.theme_style import ThemeSelector


class LiteralText:
    EMPTY_STYLE = ""
    EMPTY_TEXT = ""
    SPACE = " "
    MIN_WIDTH = 1


def literal_lines(
    text: str, width: int, style: str = LiteralText.EMPTY_STYLE
) -> FragmentLines:
    lines = []
    for source in text.splitlines() or [LiteralText.EMPTY_TEXT]:
        row, used = LiteralText.EMPTY_TEXT, 0
        for char in source:
            char = char if char.isprintable() else LiteralText.SPACE
            size = get_cwidth(char)
            if row and used + size > max(LiteralText.MIN_WIDTH, width):
                lines.append([(style, row)])
                row, used = LiteralText.EMPTY_TEXT, 0
            row += char
            used += size
        lines.append([(style, row)])
    return lines


class OutputRenderer(Protocol):
    def render(self, block: OutputBlock, context: RenderContext) -> FragmentLines: ...


class RendererCapabilities:
    REVISION = "revision"
    BLOCK_REVISION = "block_revision"
    CLOSE = "close"
    ABSENT = None
    INITIAL_REVISION = 0

    @classmethod
    def revision(cls, renderer):
        return getattr(renderer, cls.REVISION, cls.INITIAL_REVISION)

    @classmethod
    def block_revision(cls, renderer):
        return getattr(renderer, cls.BLOCK_REVISION, cls.ABSENT)

    @classmethod
    def close(cls, renderer):
        return getattr(renderer, cls.CLOSE, cls.ABSENT)


class RendererRegistry:
    MARKDOWN_KINDS = (OutputBlock.MARKDOWN, OutputBlock.CODE, OutputBlock.TABLE)
    STRUCTURED_KINDS = (
        OutputBlock.HUB_CODE,
        OutputBlock.HUB_DIFF,
        OutputBlock.HUB_TABLE,
        OutputBlock.HUB_CARD,
        OutputBlock.HUB_CHART,
    )
    ABSENT = None
    INITIAL_VERSION = 0
    REVISION_STEP = 1
    INVALID_TAG = "Output tags must use the hub- prefix"
    DUPLICATE_RENDERER = "Renderer already registered: {tag}"

    def __init__(self):
        self._renderers: dict[str, OutputRenderer] = {
            name: MarkdownRenderer() for name in self.MARKDOWN_KINDS
        }
        from .structured import StructuredRenderer

        self._renderers.update(
            {name: StructuredRenderer() for name in self.STRUCTURED_KINDS}
        )
        self._version = self.INITIAL_VERSION
        self._interactions = {}
        self.on_close = self.ABSENT

    @property
    def structure_version(self):
        return self._version

    def block_revision(self, block, context):
        renderer = self._renderers.get(block.kind)
        revision = RendererCapabilities.block_revision(renderer)
        return (
            revision(block, context)
            if revision
            else RendererCapabilities.revision(renderer)
        )

    def has_block_revisions(self, blocks):
        """Dynamic extensions must still observe their per-block dependencies."""
        return any(RendererCapabilities.block_revision(self._renderers.get(block.kind))
                   is not None for block in blocks)

    @property
    def version(self):
        return self._version, tuple(
            RendererCapabilities.revision(renderer)
            for renderer in self._renderers.values()
        )

    def register(
        self, tag: str, renderer: OutputRenderer, *, interaction=ABSENT
    ) -> None:
        if not re.fullmatch(OutputBlock.TAG_PATTERN, tag):
            raise ValueError(self.INVALID_TAG)
        if tag in self._renderers:
            raise ValueError(self.DUPLICATE_RENDERER.format(tag=tag))
        self._renderers[tag] = renderer
        if interaction is not self.ABSENT:
            self._interactions[tag] = interaction
        self._version += self.REVISION_STEP

    def create_interaction(self, item, context):
        """Create a fresh expanded UI without mutating the transcript object."""
        from ...widget.output_inspector import OutputInspector
        from ...widget.output.table_inspector import TableInspector
        from ...widget.output.code_viewer import CodeViewer

        factory = self._interactions.get(item.block.kind)
        if factory is self.ABSENT:
            factory = (
                TableInspector
                if item.block.kind in (OutputBlock.TABLE, OutputBlock.HUB_TABLE)
                else (
                    CodeViewer
                    if item.block.kind in (OutputBlock.CODE, OutputBlock.HUB_CODE)
                    else OutputInspector
                )
            )
        try:
            return factory(item, context)
        except Exception as error:
            # An extension failure must leave the source accessible and closable.
            return OutputInspector(item, context, error=str(error))

    def is_object(self, block):
        return block.kind != OutputBlock.MARKDOWN and block.kind in self._renderers

    def render(self, block: OutputBlock, context: RenderContext) -> FragmentLines:
        renderer = self._renderers.get(block.kind)
        if renderer is self.ABSENT:
            text = (
                context.language(MessageKey.OUTPUT_PENDING)
                if block.kind == OutputBlock.PENDING
                else block.raw or block.text
            )
            return literal_lines(text, context.width, ThemeSelector.MUTED.style)
        try:
            return renderer.render(block, context)
        except Exception as error:
            return literal_lines(
                context.language(MessageKey.OUTPUT_ERROR, error=str(error)),
                context.width,
                ThemeSelector.NOTICE.style,
            )

    def close(self):
        if self.on_close:
            self.on_close()
        for renderer in self._renderers.values():
            close = RendererCapabilities.close(renderer)
            if close:
                close()
