"""Shared bordered picker placement and outside-click dismissal."""
from ..core.theme import ThemeRole
from ..popups.config import PopupRect
from .interactive import click, cropped, fitted, part


class PickerOverlay:
    BORDER = 1

    @classmethod
    def draw(cls, body, columns, rows, picker, anchor, theme, locale, cancel):
        edge = cls.BORDER if min(columns, rows) > 2 * cls.BORDER else 0
        cells, height = min(columns, picker.WIDTH + edge * 2), min(rows, picker.HEIGHT + edge * 2)
        menu = picker.draw(cells - edge * 2, height - edge * 2, theme, locale)
        if edge:
            border = lambda text: part(theme, text, ThemeRole.SEPARATOR, background=ThemeRole.BUTTON_BG)
            menu = [[border("┌" + "─" * (cells - edge * 2) + "┐")],
                    *[[border("│"), *line, border("│")] for line in menu],
                    [border("└" + "─" * (cells - edge * 2) + "┘")]]
        height = len(menu)
        x = min(max(0, columns - cells), anchor[0])
        y = anchor[1] + 1 if anchor[1] + 1 + height <= rows else anchor[1] - height
        y = max(0, min(rows - height, y))
        rect = PopupRect(x + edge, y + edge, cells - edge * 2, height - edge * 2)
        dismiss = click(cancel)
        body = [[(item[0], item[1], dismiss) for item in line] for line in body]
        for index, line in enumerate(menu[:rows]):
            target = y + index
            if target < len(body):
                body[target] = [*cropped(body[target], 0, x, theme), *fitted(line, cells, theme, background=ThemeRole.BUTTON_BG),
                                *cropped(body[target], x + cells, columns - x - cells, theme)]
        return body, rect
