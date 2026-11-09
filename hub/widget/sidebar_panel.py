"""Two sidebar categories share a draggable boundary and fixed profile footer."""

from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.application.current import get_app
from .focus_region import FocusRegion
from .mouse import MouseCapture


class SidebarPanel(HSplit):
    ABSENT = None
    MIN_SECTION_ROWS = 3  # Title, rule and one selectable row.
    MIN_COMPRESSED_ROWS = 1
    SECTION_COUNT = 2
    FIRST = 0
    SECOND = 1

    def __init__(
        self, sections, profile, *, width, fraction, resize, controls=()
    ):
        self.bodies = sections
        self.controls = tuple(
            control if isinstance(control, tuple) else (control,)
            for control in controls
        )
        self.sections = (
            [
                FocusRegion(
                    section,
                    active=lambda index=index: self._focused(index),
                )
                for index, section in enumerate(sections)
            ]
            if controls
            else sections
        )
        self.profile = profile
        self.fraction, self.resize = fraction, resize
        self.geometry = self.ABSENT
        super().__init__([*self.sections, profile], width=width)

    def _focused(self, index):
        return get_app().layout.current_control in self.controls[index]

    def preferred_height(self, width, max_available_height):
        # Child heights are the last frame's allocation, not intrinsic minima.
        # Propagating them through HSplit would prevent a smaller terminal from
        # allocating this panel, so write_to_screen could never resize it.
        minimum = (self.profile.preferred_height(width, max_available_height).min
                   + self.MIN_SECTION_ROWS * self.SECTION_COUNT)
        return Dimension(min=min(minimum, max_available_height), preferred=max_available_height)

    def drag_handler(self, point):
        initial_ratio = self.fraction()
        geometry = self.geometry
        if geometry is self.ABSENT:
            return lambda position, finished=MouseCapture.DRAGGING: None
        initial, total, minimum, maximum = geometry
        origin = point.y
        last = initial

        def resize(position, finished=MouseCapture.DRAGGING):
            nonlocal last
            current = max(minimum, min(maximum, initial + position.y - origin))
            if current != last or (finished and current != initial):
                ratio = initial_ratio if current == initial else current / total
                self.resize(ratio, persist=finished and current != initial)
                get_app().invalidate()
            last = current

        return resize

    def write_to_screen(
        self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
    ):
        total, width = write_position.height, write_position.width
        profile_height = self.profile.preferred_height(width, total).preferred
        available = max(self.SECTION_COUNT, total - profile_height)
        minima = [max(self.MIN_SECTION_ROWS, section.preferred_height(width, available).min)
                  for section in self.bodies]
        if sum(minima) > available:
            minima = [max(self.MIN_COMPRESSED_ROWS, available // self.SECTION_COUNT)] * self.SECTION_COUNT
        minimum, maximum = minima[self.FIRST], available - minima[self.SECOND]
        ratio = self.fraction()
        preferred = (self.bodies[self.FIRST].preferred_height(width, available).preferred
                     if ratio is self.ABSENT else round(total * ratio))
        height = max(minimum, min(maximum, preferred))
        self.sections[self.FIRST].height = Dimension.exact(height)
        self.sections[self.SECOND].height = Dimension.exact(available - height)
        self.geometry = ((height, total, minimum, maximum)
                         if total > profile_height else self.ABSENT)
        super().write_to_screen(
            screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
        )
