"""Single-line summaries measured in terminal cells rather than characters."""

from prompt_toolkit.utils import get_cwidth
from ..backend.history_values import HistoryRow


class SummaryText:
    SPACE = " "
    EMPTY = ""
    ELLIPSIS = "…"
    ELLIPSIS_CELLS = get_cwidth(ELLIPSIS)
    MIN_WIDTH = 0
    FIRST_NUMBER = 1
    TURN_CHARACTERS = 60
    TURN = "{number}. {text} · {status}"

    @classmethod
    def collapsed(cls, text):
        return cls.SPACE.join(text.split())


def turn_label(row, index, t):
    value = HistoryRow(row)
    return SummaryText.TURN.format(
        number=index + SummaryText.FIRST_NUMBER,
        text=SummaryText.collapsed(value.text)[: SummaryText.TURN_CHARACTERS],
        status=t.status(value.status),
    )


def clipped_summary(text, width):
    text = SummaryText.collapsed(text)
    text = SummaryText.EMPTY.join(char for char in text if char.isprintable())
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
