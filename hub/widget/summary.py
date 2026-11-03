"""Single-line summaries measured in terminal cells rather than characters."""

from prompt_toolkit.utils import get_cwidth
from unicodedata import category
from ..backend.history_values import HistoryRow
from .captions import Caption


class SummaryText:
    SPACE = " "
    EMPTY = ""
    ELLIPSIS = "…"
    ELLIPSIS_CELLS = get_cwidth(ELLIPSIS)
    MIN_WIDTH = 0
    FIRST_NUMBER = 1
    PRIVATE_USE = "Co"

    @classmethod
    def collapsed(cls, text):
        return cls.SPACE.join(text.split())


def turn_label(row, index, t):
    value = HistoryRow(row)
    return Caption(
        f"{index + SummaryText.FIRST_NUMBER}. " + SummaryText.collapsed(value.text),
        t.status(value.status),
    ).render()


def clipped_summary(text, width):
    text = SummaryText.collapsed(text)
    return clipped_line(text, width)


def clipped_line(text, width):
    """Clip a display row without removing its indentation."""
    # NerdFont uses private-use code points, which str.isprintable rejects.
    # Preserve those glyphs while still excluding terminal control bytes.
    text = SummaryText.EMPTY.join(char for char in text
                                 if char.isprintable() or category(char) == SummaryText.PRIVATE_USE)
    width = max(SummaryText.MIN_WIDTH, width)
    if get_cwidth(text) <= width:
        return text
    if not width:
        return SummaryText.EMPTY
    result, used = [], SummaryText.MIN_WIDTH
    for char in text:
        cells = get_cwidth(char)
        if used + cells > width - SummaryText.ELLIPSIS_CELLS:
            break
        result.append(char)
        used += cells
    return SummaryText.EMPTY.join(result) + SummaryText.ELLIPSIS
