"""Pure rendered-text indexing, suitable for the shared render worker."""

from bisect import bisect_right
import re
from typing import NamedTuple
from ..input_contract import ControlFragment
from ...config.theme_style import ThemeSelector, ThemeStyle


class SearchPattern:
    EMPTY = ""
    NEWLINE = "\n"
    ESCAPED_SPACE = r"\ "
    WHITESPACE = r"\s+"


class SearchKey(NamedTuple):
    source: object
    query: str


class SearchHit(NamedTuple):
    row: int
    start: int
    end: int


class SearchSpan(NamedTuple):
    index: int
    start: int
    end: int


class IndexedLines(NamedTuple):
    hits: tuple
    by_line: dict

    @classmethod
    def empty(cls):
        return cls((), {})


class SearchHighlight:
    @staticmethod
    def apply(fragments, spans, current):
        result, offset = [], 0
        for style, text in fragments:
            end = offset + len(text)
            cursor = offset
            for index, left, right in spans:
                left, right = max(offset, left), min(end, right)
                if left >= right:
                    continue
                if cursor < left:
                    result.append((style, text[cursor - offset : left - offset]))
                highlight = (
                    ThemeSelector.SEARCH_CURRENT.style
                    if index == current()
                    else ThemeSelector.SEARCH_MATCH.style
                )
                result.append(
                    (
                        style + ThemeStyle.MODIFIER_SEPARATOR + highlight,
                        text[left - offset : right - offset],
                    )
                )
                cursor = right
            if cursor < end:
                result.append((style, text[cursor - offset :]))
            offset = end
        return result


def index_lines(lines, query):
    hits, by_line = [], {}
    pattern = re.compile(
        re.escape(query).replace(SearchPattern.ESCAPED_SPACE, SearchPattern.WHITESPACE),
        re.IGNORECASE,
    )
    texts, starts, offset = [], [], 0
    for fragments in lines:
        text = SearchPattern.EMPTY.join(
            ControlFragment.text(fragment) for fragment in fragments
        )
        starts.append(offset)
        texts.append(text)
        offset += len(text) + len(SearchPattern.NEWLINE)
    for match in pattern.finditer(SearchPattern.NEWLINE.join(texts)):
        first = max(0, bisect_right(starts, match.start()) - 1)
        last = max(first, bisect_right(starts, match.end() - 1) - 1)
        index = len(hits)
        hits.append(
            SearchHit(first, match.start() - starts[first], match.end() - starts[first])
        )
        for row in range(first, last + 1):
            start, end = max(0, match.start() - starts[row]), min(
                len(texts[row]), match.end() - starts[row]
            )
            by_line.setdefault(row, []).append(SearchSpan(index, start, end))
    return IndexedLines(
        tuple(hits), {row: tuple(values) for row, values in by_line.items()}
    )
