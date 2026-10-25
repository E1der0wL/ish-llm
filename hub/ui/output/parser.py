"""Standalone hub-* XML elements embedded between Markdown blocks."""

import re
from xml.etree import ElementTree
from .markdown_syntax import MarkdownSyntax

from .model import OutputBlock, CodeAttributes


class OutputParser:
    MAX_BLOCK_CHARS = 65536
    KEEP_ENDS = True
    FINAL = True
    ABSENT = None
    EMPTY = ""
    INITIAL_BYTES = 0
    FENCE_PATTERN = r"^ {0,3}(`{3,}|~{3,})"
    TAG_PATTERN = r"^ {{0,3}}<({tag})(?=[\s/>])"
    END_PATTERN = r"(?:/>|</{tag}>)\s*$"
    XML_DECLARATION = "<!"
    XML_END = ">"
    UNSUPPORTED_DECLARATION = "Unsupported XML declaration"
    NESTED_ELEMENTS = "Nested output elements are not supported"

    def __init__(self):
        self._parser = MarkdownSyntax.table_parser()
        self.parsed_bytes = self.INITIAL_BYTES

    def _markdown(self, text, start):
        lines = text.splitlines(keepends=self.KEEP_ENDS)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        cursor = 0
        for token in self._parser.parse(text):
            if (
                token.level
                or token.type not in MarkdownSyntax.BLOCK_TOKENS
                or token.map is self.ABSENT
            ):
                continue
            left, right = (offsets[index] for index in token.map)
            if left > cursor:
                yield OutputBlock(
                    OutputBlock.MARKDOWN, text[cursor:left], start + cursor
                )
            kind = (
                OutputBlock.TABLE
                if token.type == MarkdownSyntax.TABLE_OPEN
                else OutputBlock.CODE
            )
            raw = text[left:right]
            yield OutputBlock(
                kind,
                raw,
                start + left,
                CodeAttributes(token.info.strip()).to_pairs(),
                raw,
            )
            cursor = right
        if cursor < len(text):
            yield OutputBlock(OutputBlock.MARKDOWN, text[cursor:], start + cursor)

    def parse(self, text: str, *, final: bool = FINAL) -> tuple[OutputBlock, ...]:
        self.parsed_bytes += len(text.encode(OutputBlock.ENCODING))
        lines = text.splitlines(keepends=self.KEEP_ENDS)
        blocks, offset, start, plain = [], 0, 0, []
        fence = self.ABSENT
        index = 0

        def flush():
            if plain:
                blocks.extend(self._markdown(self.EMPTY.join(plain), start))
                plain.clear()

        while index < len(lines):
            line = lines[index]
            marker = re.match(self.FENCE_PATTERN, line)
            if marker:
                token = marker[1]
                if fence is self.ABSENT:
                    fence = token
                elif (
                    token[0] == fence[0]
                    and len(token) >= len(fence)
                    and not line[marker.end() :].strip()
                ):
                    fence = self.ABSENT
            tag = (
                self.ABSENT
                if fence or marker
                else re.match(
                    self.TAG_PATTERN.format(tag=OutputBlock.TAG_PATTERN), line
                )
            )
            if tag:
                flush()
                raw = line
                # Only whole, standalone elements are interpreted. A finite
                # input bound also keeps unfinished streamed tags inexpensive.
                while (
                    not re.search(self.END_PATTERN.format(tag=re.escape(tag[1])), raw)
                    and index + 1 < len(lines)
                    and len(raw) < self.MAX_BLOCK_CHARS
                ):
                    index += 1
                    raw += lines[index]
                kind, content, attrs = OutputBlock.LITERAL, raw, ()
                try:
                    if len(raw) > self.MAX_BLOCK_CHARS or self.XML_DECLARATION in raw:
                        raise ValueError(self.UNSUPPORTED_DECLARATION)
                    element = ElementTree.fromstring(raw.strip())
                    if element.tag != tag[1] or len(element):
                        raise ValueError(self.NESTED_ELEMENTS)
                    kind, content, attrs = (
                        element.tag,
                        element.text or self.EMPTY,
                        tuple(element.attrib.items()),
                    )
                except (ElementTree.ParseError, ValueError):
                    if (
                        not final
                        and index == len(lines) - 1
                        and not raw.rstrip().endswith(self.XML_END)
                    ):
                        kind, content = OutputBlock.PENDING, self.EMPTY
                blocks.append(OutputBlock(kind, content, offset, attrs, raw))
                offset += len(raw)
                start = offset
            else:
                if not plain:
                    start = offset
                plain.append(line)
                offset += len(line)
            index += 1
        flush()
        return tuple(blocks)
