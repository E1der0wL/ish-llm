"""Route a page's wheel events without moving keyboard focus or stealing clicks."""

from prompt_toolkit.layout import HSplit
from prompt_toolkit.mouse_events import MouseEventType


class WheelRoutingContainer(HSplit):
    def __init__(self, content, handler, *, enabled=lambda: True):
        super().__init__([content])
        self.handler, self.enabled = handler, enabled

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        super().write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)
        if not self.enabled():
            return
        def route(original):
            def handle(event):
                if self.enabled() and event.event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
                    return self.handler(event)
                return original(event)
            return handle
        routed = {}
        for y in range(write_position.ypos, write_position.ypos + write_position.height):
            row = mouse_handlers.mouse_handlers[y]
            for x in range(write_position.xpos, write_position.xpos + write_position.width):
                original = row[x]
                if original not in routed:
                    routed[original] = route(original)
                row[x] = routed[original]
