"""Shared message layout; features retain action and domain transition ownership."""
from collections.abc import Sequence

from ..core.theme import Theme, ThemeRole
from ..locales import Locale, Message as M
from ..popups.models import PopupAction
from .interactive import button, centered, fitted, part
from .presentation import TextRow
from .text import clusters, display_text, fit, fit_labels, width


class MessageForm:
    """Scrollable message/details with a fixed heading and action footer.

    This owns only presentation state. Callers supply translated text, the
    selected action and callbacks; rendering never invokes a callback.
    """

    HEADER_ROWS = 1
    SECTION_GAP = 1
    FOOTER_PADDING = 1
    ACTION_GAP = " "

    def __init__(self):
        self._offset = self._maximum = 0
        self._page_rows = 1
        self._content_key = self._wrapped_key = None
        self._wrapped = []

    @staticmethod
    def heading(text: str, cells: int, theme: Theme, *, background=ThemeRole.BACKGROUND):
        return fitted([part(theme, text, ThemeRole.TITLE, background=background, bold=True)] if text else [],
                      cells, theme, background=background)

    def scroll(self, amount: int) -> None:
        self._offset = max(0, min(self._maximum, self._offset + amount))

    @staticmethod
    def status_text(locale: Locale) -> str:
        return locale.text(M.CONFIRM_HINT)

    def key(self, key: str) -> bool:
        if key in ("up", "scroll-up", "down", "scroll-down"):
            self.scroll(-1 if key in ("up", "scroll-up") else 1)
        elif key in ("pageup", "pagedown"):
            self.scroll(self._page_rows * (-1 if key == "pageup" else 1))
        elif key in ("home", "end"):
            self._offset = 0 if key == "home" else self._maximum
        else:
            return False
        return True

    @staticmethod
    def _wrap(rows: Sequence[TextRow], cells: int, theme: Theme, background):
        result = []
        for row in rows:
            fragments, used = [], 0
            for span in row.spans:
                for index, line in enumerate(span.text.split("\n")):
                    if index:
                        result.append(fitted(fragments, cells, theme, background=background))
                        fragments, used = [], 0
                    for cluster in clusters(display_text(line)):
                        size = width(cluster)
                        if used and used + size > cells:
                            result.append(fitted(fragments, cells, theme, background=background))
                            fragments, used = [], 0
                        # A wide glyph cannot fit a one-cell viewport.
                        text = cluster if size <= cells else fit(cluster, cells)
                        item = part(theme, text, span.color or ThemeRole.FOREGROUND,
                                    background=background, bold=span.bold)
                        if fragments and fragments[-1][0] == item[0]:
                            fragments[-1] = (item[0], fragments[-1][1] + text)
                        else:
                            fragments.append(item)
                        used += width(text)
            result.append(fitted(fragments, cells, theme, background=background))
        return result

    @classmethod
    def _buttons(cls, actions, selected, cells, theme, background):
        labels = fit_labels([" " + action.label + " " for action in actions], cells, cls.ACTION_GAP)
        indices = list(range(len(actions)))
        if any(width(label) < 3 for label in labels):
            indices, labels = [selected], [fit(" " + actions[selected].label + " ", cells)]
        parts = []
        for index, label in zip(indices, labels):
            if parts:
                parts.append(part(theme, cls.ACTION_GAP, background=background))
            item = button(theme, actions[index].label, selected=index == selected, action=actions[index].invoke)
            parts.append((item[0], label, *item[2:]))
        return centered(parts, cells, theme, background=background)

    def draw(self, message: TextRow, cells: int, height: int, theme: Theme, *,
             details: Sequence[TextRow] = (), actions: Sequence[PopupAction] = (), selected: int = 0,
             heading: str | None = "", background=ThemeRole.BACKGROUND):
        """None omits the heading row; empty text explicitly reserves a blank row."""
        if cells <= 0 or height <= 0:
            return []
        content_key = (message, tuple(details))
        if content_key != self._content_key:
            self._content_key, self._offset = content_key, 0
        wrapped_key = (content_key, cells, theme, background)
        if wrapped_key != self._wrapped_key:
            rows = [message]
            if details:
                rows.extend([TextRow()] * self.SECTION_GAP)
                rows.extend(details)
            self._wrapped = self._wrap(rows, cells, theme, background)
            self._wrapped_key = wrapped_key
        blank = lambda: fitted([], cells, theme, background=background)
        # Keep an actionable button even when the viewport cannot fit padding.
        button_rows = int(bool(actions))
        header_rows = min(self.HEADER_ROWS if heading is not None else 0, max(0, height - button_rows - 1))
        spare = max(0, height - header_rows - button_rows - 1)
        bottom = min(self.FOOTER_PADDING, spare) if actions else 0
        top = min(self.FOOTER_PADDING, spare - bottom) if actions else 0
        self._page_rows = max(0, height - header_rows - button_rows - top - bottom)
        self._maximum = max(0, len(self._wrapped) - self._page_rows)
        self._offset = min(self._offset, self._maximum)
        rows = [self.heading(heading, cells, theme, background=background)] if header_rows else []
        body = self._wrapped[self._offset:self._offset + self._page_rows]
        rows += body + [blank() for _ in range(self._page_rows - len(body))]
        if actions:
            rows += [blank() for _ in range(top)]
            rows.append(self._buttons(actions, selected % len(actions), cells, theme, background))
            rows += [blank() for _ in range(bottom)]
        return rows
