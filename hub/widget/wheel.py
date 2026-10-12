"""Route a page's wheel events without moving keyboard focus or stealing clicks."""

from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.mouse_events import MouseEventType


class RoutedMouseHandlers(MouseHandlers):
    """Wrap registrations too, including Windows painted later at a z-index."""

    def __init__(self, target, handler, enabled):
        self.mouse_handlers = target.mouse_handlers
        self.handler, self.enabled = handler, enabled
        self._routed = {}

    def set_mouse_handler_for_range(self, x_min, x_max, y_min, y_max, handler):
        if handler not in self._routed:
            def route(event):
                if self.enabled() and event.event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
                    return self.handler(event)
                return handler(event)
            self._routed[handler] = route
        super().set_mouse_handler_for_range(x_min, x_max, y_min, y_max, self._routed[handler])


class WheelRoutingContainer(HSplit):
    def __init__(self, content, handler, *, enabled=lambda: True):
        super().__init__([content])
        self.handler, self.enabled = handler, enabled

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        handlers = RoutedMouseHandlers(mouse_handlers, self.handler, self.enabled)
        super().write_to_screen(screen, handlers, write_position, parent_style, erase_bg, z_index)
