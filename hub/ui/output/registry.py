"""Explicit renderer registration; tags never name importable Python classes."""

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
        self._renderers: dict[str, OutputRenderer] = {name: MarkdownRenderer() for name in ("markdown", "code", "table")}
        from .structured import StructuredRenderer
        self._renderers.update({name: StructuredRenderer() for name in
                               ("hub-code", "hub-diff", "hub-table", "hub-card", "hub-chart")})
        self._version = 0

    @property
    def version(self):
        return self._version, tuple(getattr(renderer, "revision", 0) for renderer in self._renderers.values())

    def register(self, tag: str, renderer: OutputRenderer) -> None:
        if not re.fullmatch(r"hub-[a-z][a-z0-9-]*", tag):
            raise ValueError("Output tags must use the hub- prefix")
        if tag in self._renderers:
            raise ValueError(f"Renderer already registered: {tag}")
        self._renderers[tag] = renderer
        self._version += 1

    def is_object(self, block):
        return block.kind != "markdown" and block.kind in self._renderers

    def render(self, block: OutputBlock, context: RenderContext) -> FragmentLines:
        renderer = self._renderers.get(block.kind)
        if renderer is None:
            text = context.language("output_pending") if block.kind == "pending" else block.raw or block.text
            return literal_lines(text, context.width, "class:hub.muted")
        try:
            return renderer.render(block, context)
        except Exception as error:
            return literal_lines(context.language("output_error", error=str(error)), context.width, "class:hub.notice")

    def close(self):
        for renderer in self._renderers.values():
            close = getattr(renderer, "close", None)
            if close:
                close()
