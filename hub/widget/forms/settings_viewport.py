"""Keep all field controls navigable, but paint only visible settings rows."""

from bisect import bisect_right

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.screen import WritePosition

from ..controls import ScrollablePane
from ..input_contract import ContainerControl


class SettingsBody(HSplit):
    NO_VIEWPORT = None
    UNMEASURED = None
    NO_CONTROL_ROW = None
    MAX_MEASURE_HEIGHT = 10000

    def __init__(self, children, fields):
        super().__init__(children)
        self.fields = fields
        self.viewport = self.NO_VIEWPORT
        self._measure_key = self.UNMEASURED
        self._offsets = [0]
        self._control_rows = {
            ContainerControl.required(window): index
            for index, child in enumerate(self.children)
            for window in walk(child)
            if ContainerControl.has_focusable_content(window)
        }

    def _measure(self, width):
        key = (width, tuple((field.input.text, field.editing) for field in self.fields))
        if key != self._measure_key:
            offsets = [0]
            for child in self.children:
                offsets.append(
                    offsets[-1]
                    + child.preferred_height(width, self.MAX_MEASURE_HEIGHT).preferred
                )
            self._offsets, self._measure_key = offsets, key
        return self._offsets

    def preferred_height(self, width, max_available_height):
        return Dimension.exact(self._measure(width)[-1])

    def write_to_screen(
        self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
    ):
        offsets = self._measure(write_position.width)
        if self.viewport is self.NO_VIEWPORT:
            indices = range(len(self.children))
        else:
            top, height = self.viewport
            ranges = [(top, top + height)]
            focused = self._control_rows.get(get_app().layout.current_control)
            if focused is not self.NO_CONTROL_ROW:
                # ScrollablePane follows focus after this render. Also paint the
                # destination so a jump to a distant field has no blank frame.
                ranges.append(
                    (
                        offsets[focused] - height,
                        offsets[focused + 1] + height,
                    )
                )
            indices = set()
            for start, end in ranges:
                first = max(
                    0, bisect_right(offsets, start) - 1
                )
                last = min(len(self.children), bisect_right(offsets, end))
                indices.update(range(first, last))
            indices = sorted(indices)
        for index in indices:
            self.children[index].write_to_screen(
                screen,
                mouse_handlers,
                WritePosition(
                    xpos=write_position.xpos,
                    ypos=write_position.ypos + offsets[index],
                    width=write_position.width,
                    height=offsets[index + 1] - offsets[index],
                ),
                parent_style,
                erase_bg,
                z_index,
            )


class SettingsPane(ScrollablePane):
    SCROLLBAR_GAP = 1

    def __init__(self, content, **kwargs):
        self.body = to_container(content)
        self.scrollbar_gap = Window(width=self.SCROLLBAR_GAP)
        super().__init__(VSplit([self.body, self.scrollbar_gap]), **kwargs)

    def write_to_screen(
        self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
    ):
        body = self.body.get_children()[
            0
        ]  # SettingsScreen's DynamicContainer.
        if isinstance(body, SettingsBody):
            body.viewport = (self.vertical_scroll, write_position.height)
        super().write_to_screen(
            screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
        )
