"""Sidebar sections share a fixed profile footer and bounded geometry."""

from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension


class SidebarPanel(HSplit):
    def __init__(self, sections, profile, *, width, project_fraction=None):
        self.sections = sections
        self.project_fraction = project_fraction
        super().__init__([*sections, profile], width=width)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        if self.project_fraction is not None:
            # The project category includes its heading; keep 35% on terminal resize.
            height = min(max(1, round(write_position.height * self.project_fraction)),
                         max(1, write_position.height - 6))
            self.sections[0].height = Dimension.exact(height)
        super().write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)
