"""Shared horizontal button navigation and responsive, centered action rows."""

from prompt_toolkit.layout import DynamicContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension


def bind_button_navigation(buttons):
    """Keep button arrows and their footer hints together, including edge keys."""
    def move(event):
        controls = [button.control for button in buttons]
        current = event.app.layout.current_control
        if current in controls:
            index = controls.index(current) + (-1 if event.key_sequence[-1].key == "left" else 1)
            event.app.layout.focus(controls[max(0, min(len(controls) - 1, index))])

    for button in buttons:
        if getattr(button.control, "hub_button_group", None) is buttons:
            continue
        button.control.hub_button_group = buttons
        registry = button.control.hub_shortcuts
        registry.add(["left", "right"], "←→", "select", move, when=lambda: len(buttons) > 1)
        registry.add(["up", "down"], "", "", lambda e: None)


class ActionBar(DynamicContainer):
    def __init__(self, buttons, width):
        self.buttons, self.available_width = buttons, width
        bind_button_navigation(buttons)
        super().__init__(self._container)

    def insert(self, index, button):
        self.buttons.insert(index, button)
        bind_button_navigation(self.buttons)

    def rows(self, width=None):
        width = self.available_width() if width is None else width
        rows, row, used = [], [], 0
        for button in self.buttons:
            if row and used + button.width + 1 > width:
                rows.append(row)
                row, used = [], 0
            used += button.width + bool(row)
            row.append(button)
        return [*rows, row] if row else rows

    def preferred_width(self, max_available_width):
        minimum = max((button.width for button in self.buttons), default=0)
        return Dimension(min=min(minimum, max_available_width),
                         preferred=min(self.available_width(), max_available_width))

    def preferred_height(self, width, max_available_height):
        return self._container(width).preferred_height(width, max_available_height)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        self._container(write_position.width).write_to_screen(
            screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)

    def _container(self, width=None):
        return HSplit([VSplit([
            Window(width=Dimension(weight=1)),
            VSplit(row, padding=1, width=Dimension.exact(sum(button.width for button in row) + len(row) - 1)),
            Window(width=Dimension(weight=1)),
        ]) for row in self.rows(width)])
