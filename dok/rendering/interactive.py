"""Interactive popup contract and cell-aware fragment building helpers."""
from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from .text import fit, width, clusters
from ..locales import Locale, Message
from ..core.theme import ThemeRole


class InteractivePopup:
    """A page owns local editing; the host retains one modal focus control."""
    def key(self, key: str, data: str) -> bool:
        return False

    def request_close(self) -> bool:
        return True

    def status_text(self) -> str:
        """Current area's key map, resolved again on every redraw."""
        return Locale().text(Message.ESC_CLOSE)

    def draw_body(self, columns, rows, theme):
        raise NotImplementedError

    def dispose(self):
        """Release page-owned work when the host replaces or closes the page."""


def click(callback):
    def handle(event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            callback()
        return None
    return handle


def part(theme, text, role=ThemeRole.FOREGROUND, *, background=ThemeRole.BACKGROUND, bold=False, action=None):
    item = (theme.span(role, background=background, bold=bold), text)
    return (*item, click(action)) if action else item


def button(theme, label, *, role=ThemeRole.FOREGROUND, selected=False, bold=False, action=None):
    item = (theme.button(role, selected=selected, bold=bold), " " + label + " ")
    return (*item, click(action)) if action else item


def fitted(parts, cells, theme, *, background=ThemeRole.BACKGROUND):
    result, used = [], 0
    for item in parts:
        text = fit(item[1], max(0, cells - used), marker="")
        result.append((item[0], text, *item[2:]))
        used += width(text)
    if used < cells:
        result.append(part(theme, " " * (cells - used), background=background))
    return result


def centered(parts, cells, theme, *, background=ThemeRole.BACKGROUND):
    return fitted([part(theme, " " * max(0, (cells - sum(width(p[1]) for p in parts)) // 2), background=background), *parts], cells, theme, background=background)


def cropped(parts, start, cells, theme):
    """Slice display cells while preserving styles and mouse callbacks."""
    result, position = [], 0
    for item in parts:
        text = ""
        for cluster in clusters(item[1]):
            size = width(cluster)
            if position >= start and position + size <= start + cells:
                text += cluster
            elif position < start + cells and position + size > start:
                text += " " * (min(position + size, start + cells) - max(position, start))
            position += size
        if text:
            result.append((item[0], text, *item[2:]))
    return fitted(result, cells, theme)
