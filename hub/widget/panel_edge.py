"""Shared nonfocusable resize edge for two-panel pages."""

from prompt_toolkit.layout import Window

from .mouse import hover_style
from ..config.theme_style import ThemeSelector


class PanelEdge(Window):
    WIDTH = 1
    GLYPH = "│"
    DRAGGING = False

    def __init__(self, width, resize):
        super().__init__(
            width=self.WIDTH, char=self.GLYPH, style=ThemeSelector.DIVIDER.style
        )
        self.panel_width, self.resize_panel = width, resize
        hover_style(self)

    def drag_handler(self, point):
        initial, origin = self.panel_width(), point.x

        def resize(position, finished=self.DRAGGING):
            self.resize_panel(initial + position.x - origin, persist=finished)

        return resize
