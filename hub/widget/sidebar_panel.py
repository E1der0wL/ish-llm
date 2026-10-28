"""Sidebar sections share a fixed profile footer and bounded geometry."""

from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.application.current import get_app
from .focus_region import FocusRegion


class SidebarPanel(HSplit):
    ABSENT = None
    MIN_SECTION_ROWS = 1
    RESERVED_ROWS = 6

    def __init__(
        self, sections, profile, *, width, project_fraction=ABSENT, controls=()
    ):
        self.sections = (
            [
                FocusRegion(
                    section,
                    active=lambda control=control: get_app().layout.current_control
                    in (control if isinstance(control, tuple) else (control,)),
                )
                for section, control in zip(sections, controls)
            ]
            if controls
            else sections
        )
        self.project_fraction = project_fraction
        super().__init__([*self.sections, profile], width=width)

    def write_to_screen(
        self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
    ):
        if self.project_fraction is not self.ABSENT:
            # The project category includes its heading and follows terminal resize.
            height = min(
                max(
                    self.MIN_SECTION_ROWS,
                    round(write_position.height * self.project_fraction),
                ),
                max(self.MIN_SECTION_ROWS, write_position.height - self.RESERVED_ROWS),
            )
            self.sections[0].height = Dimension.exact(height)
        super().write_to_screen(
            screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
        )
