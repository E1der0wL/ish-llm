"""Reuse sealed output blocks while keeping unfinished Markdown canonical."""

from dataclasses import dataclass, replace
import re

from .model import OutputBlock
from .parser import OutputParser


@dataclass(frozen=True, slots=True)
class StreamingParse:
    text: str = ""
    end: int = 0
    prefix: tuple[OutputBlock, ...] = ()

    @staticmethod
    def sealed(block: OutputBlock) -> bool:
        raw = block.raw or block.text
        if not raw.endswith("\n"):
            return False
        if block.kind.startswith(OutputBlock.ICON_PREFIX):
            return True
        if block.kind != OutputBlock.CODE:
            return False
        lines = raw.splitlines()
        opening = re.match(OutputParser.FENCE_PATTERN, lines[0])
        if opening is None or len(lines) < 2:
            return False
        marker = opening[1]
        return re.fullmatch(r" {0,3}" + re.escape(marker[0]) +
                            "{" + str(len(marker)) + r",}[ \t]*", lines[-1]) is not None

    def parse(self, parser: OutputParser, text: str, *, final: bool):
        # Replacements, truncation and final publication always reconcile with
        # the canonical parser. No streamed draft is an authoritative result.
        reusable = not final and text.startswith(self.text)
        end, prefix = (self.end, self.prefix) if reusable else (0, ())
        tail = parser.parse(text[end:], final=final)
        shifted = tuple(replace(block, start=block.start + end) for block in tail)
        blocks = prefix + shifted
        if final:
            return blocks, StreamingParse()
        for index in range(len(shifted) - 1, -1, -1):
            block = shifted[index]
            if self.sealed(block):
                end = block.start + len(block.raw or block.text)
                prefix = prefix + shifted[:index + 1]
                break
        return blocks, StreamingParse(text, end, prefix)
