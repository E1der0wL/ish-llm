"""Responsive, centered button rows shared by settings pages."""

from prompt_toolkit.layout import DynamicContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension


class ActionBar(DynamicContainer):
    def __init__(self, buttons, width):
        self.buttons, self.available_width = buttons, width
        super().__init__(self._container)

    def rows(self):
        rows, row, used = [], [], 0
        for button in self.buttons:
            if row and used + button.width + 1 > self.available_width():
                rows.append(row)
                row, used = [], 0
            used += button.width + bool(row)
            row.append(button)
        return [*rows, row] if row else rows

    def _container(self):
        return HSplit([VSplit([
            Window(width=Dimension(weight=1)),
            VSplit(row, padding=1, width=Dimension.exact(sum(button.width for button in row) + len(row) - 1)),
            Window(width=Dimension(weight=1)),
        ]) for row in self.rows()])
