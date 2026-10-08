"""Standalone hub-* XML elements embedded between Markdown blocks."""

import re
from xml.etree import ElementTree
from markdown_it import MarkdownIt

from .model import OutputBlock


class OutputParser:
    def _markdown(self, text, start):
        lines = text.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        cursor = 0
        for token in MarkdownIt("commonmark").enable("table").parse(text):
            if token.level or token.type not in ("fence", "code_block", "table_open") or token.map is None:
                continue
            left, right = (offsets[index] for index in token.map)
            if left > cursor:
                yield OutputBlock("markdown", text[cursor:left], start + cursor)
            kind = "table" if token.type == "table_open" else "code"
            raw = text[left:right]
            yield OutputBlock(kind, raw, start + left, (("language", token.info.strip()),), raw)
            cursor = right
        if cursor < len(text):
            yield OutputBlock("markdown", text[cursor:], start + cursor)

    def parse(self, text: str, *, final: bool = True) -> tuple[OutputBlock, ...]:
        lines = text.splitlines(keepends=True)
        blocks, offset, start, plain = [], 0, 0, []
        fence = None
        index = 0
        def flush():
            if plain:
                blocks.extend(self._markdown("".join(plain), start))
                plain.clear()
        while index < len(lines):
            line = lines[index]
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if marker:
                token = marker[1]
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                    fence = None
            tag = None if fence or marker else re.match(r"^ {0,3}<(hub-[a-z][a-z0-9-]*)(?=[\s/>])", line)
            if tag:
                flush()
                raw = line
                # Only whole, standalone elements are interpreted. A finite
                # input bound also keeps unfinished streamed tags inexpensive.
                while (not re.search(r"(?:/>|</" + re.escape(tag[1]) + r">)\s*$", raw)
                       and index + 1 < len(lines) and len(raw) < 65536):
                    index += 1
                    raw += lines[index]
                kind, content, attrs = "literal", raw, ()
                try:
                    if len(raw) > 65536 or "<!" in raw:
                        raise ValueError("Unsupported XML declaration")
                    element = ElementTree.fromstring(raw.strip())
                    if element.tag != tag[1] or len(element):
                        raise ValueError("Nested output elements are not supported")
                    kind, content, attrs = element.tag, element.text or "", tuple(element.attrib.items())
                except (ElementTree.ParseError, ValueError):
                    if not final and index == len(lines) - 1 and not raw.rstrip().endswith(">"):
                        kind, content = "pending", ""
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
