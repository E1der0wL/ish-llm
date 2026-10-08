"""Single-line summaries measured in terminal cells rather than characters."""

from prompt_toolkit.utils import get_cwidth


def clipped_summary(text, width):
    text = " ".join(text.split())
    text = "".join(char for char in text if char.isprintable())
    width = max(0, width)
    if get_cwidth(text) <= width:
        return text
    if not width:
        return ""
    result, used = [], 0
    for char in text:
        cells = get_cwidth(char)
        if used + cells > width - 1:
            break
        result.append(char)
        used += cells
    return "".join(result) + "…"
