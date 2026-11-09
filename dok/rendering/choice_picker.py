"""Keyboard/mouse enum dropdown using the shared popup menu palette."""
from ..core.theme import ThemeRole
from ..locales import Message as M
from .interactive import part, fitted
from .text import fit, width


class ChoicePicker:
    def __init__(self, values, current, label, apply, cancel, changed):
        self.values, self.label = tuple(values), label
        self.index = self.values.index(current)
        self._apply, self._cancel, self._changed = apply, cancel, changed
        self.WIDTH = max(width(label(value)) for value in self.values) + 3
        self.HEIGHT = len(self.values)

    def key(self, key, data):
        if key == "escape":
            self._cancel()
        elif key in ("up", "down", "tab", "s-tab"):
            self.index = (self.index + (-1 if key in ("up", "s-tab") else 1)) % len(self.values)
        elif key == "enter" or data == " ":
            self._apply(self.values[self.index])
        self._changed()
        return True

    def scroll_at(self, x, y, amount):
        self.index = max(0, min(len(self.values) - 1, self.index + amount))
        self._changed()

    def status_text(self, locale):
        return locale.text(M.MENU_HINT)

    def draw(self, cells, rows, theme, locale):
        start = max(0, self.index - rows + 1)
        result = []
        for index in range(start, min(len(self.values), start + rows)):
            selected = index == self.index
            role, bg = (ThemeRole.SELECTED_FG, ThemeRole.SELECTED_BG) if selected else (ThemeRole.MENU_TEXT, ThemeRole.BUTTON_BG)
            label = fit(" " + self.label(self.values[index]), max(0, cells - 1))
            result.append(fitted([part(theme, label, role, background=bg,
                action=lambda index=index: self._apply(self.values[index])),
                part(theme, " " * max(0, cells - 1 - width(label)), role, background=bg,
                     action=lambda index=index: self._apply(self.values[index])),
                part(theme, "█" if selected else " ", ThemeRole.SCROLLBAR, background=ThemeRole.BUTTON_BG)], cells, theme))
        return result
