"""Shared nonfocusable resize edge for two-panel pages."""

from prompt_toolkit.layout import Window

from .mouse import hover_style


class PanelEdge(Window):
    def __init__(self, width, resize):
        super().__init__(width=1, char="│", style="class:hub.divider")
        self.panel_width, self.resize_panel = width, resize
        hover_style(self)

    def drag_handler(self, point):
        initial, origin = self.panel_width(), point.x

        def resize(position, finished=False):
            self.resize_panel(initial + position.x - origin, persist=finished)

        return resize
