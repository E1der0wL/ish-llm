"""Pure rendered-text indexing, suitable for the shared render worker."""

from bisect import bisect_right
import re


def index_lines(lines, query):
    hits, by_line = [], {}
    pattern = re.compile(re.escape(query).replace(r"\ ", r"\s+"), re.IGNORECASE)
    texts, starts, offset = [], [], 0
    for fragments in lines:
        text = "".join(fragment[1] for fragment in fragments)
        starts.append(offset)
        texts.append(text)
        offset += len(text) + 1
    for match in pattern.finditer("\n".join(texts)):
        first = max(0, bisect_right(starts, match.start()) - 1)
        last = max(first, bisect_right(starts, match.end() - 1) - 1)
        index = len(hits)
        hits.append((first, match.start() - starts[first], match.end() - starts[first]))
        for row in range(first, last + 1):
            start, end = max(0, match.start() - starts[row]), min(len(texts[row]), match.end() - starts[row])
            by_line.setdefault(row, []).append((index, start, end))
    return tuple(hits), {row: tuple(values) for row, values in by_line.items()}
