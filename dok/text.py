"""Terminal text width, display sanitization and cluster-preserving truncation."""

import unicodedata

from prompt_toolkit.utils import get_cwidth


def display_text(text: str) -> str:
    return "".join(" " if unicodedata.category(char) in {"Cc", "Zl", "Zp"} else char for char in text)


def clusters(text: str):
    """Keep combining marks, emoji modifiers, ZWJ chains and flag pairs together."""
    current = ""
    regional = 0
    for char in text:
        point = ord(char)
        is_regional = 0x1F1E6 <= point <= 0x1F1FF
        joins = (unicodedata.category(char).startswith("M") or char == "\u200d"
                 or current.endswith("\u200d") or 0x1F3FB <= point <= 0x1F3FF
                 or 0xE0020 <= point <= 0xE007F or is_regional and regional % 2 == 1)
        if current and not joins:
            yield current
            current = ""
        current += char
        regional = regional + 1 if is_regional else 0
    if current:
        yield current


def width(text: str) -> int:
    # prompt-toolkit retains short strings indefinitely. Animated rows have an
    # unbounded number of combinations; cache only their individual characters.
    return sum(get_cwidth(char) for char in text)


def fit(text: str, cells: int, marker="…") -> str:
    text = display_text(text)
    if width(text) <= cells:
        return text
    room = max(0, cells - width(marker))
    result = ""
    for cluster in clusters(text):
        if width(result) + width(cluster) > room:
            break
        result += cluster
    return result + (marker if width(marker) <= cells else "")
