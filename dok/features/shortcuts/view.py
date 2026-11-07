"""Shortcut label fitting and mouse submission through the supplied callback."""

from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from ...rendering.presentation import RenderContext, swallow_mouse
from ...rendering.text import display_text, fit_labels, width
from .config import Shortcut
from ...core.colors import DockPalette


class ShortcutView:
    def __init__(self, config, execute=None):
        self.element = config.widgets.shortcuts
        self._config = self.element
        self._execute = execute

    def _handler(self, shortcut):
        def handler(event):
            if (self._execute is not None and event.event_type == MouseEventType.MOUSE_UP
                    and event.button == MouseButton.LEFT):
                self._execute(shortcut.slot)
            return None
        return handler

    def render(self, context: RenderContext) -> tuple:
        slots = [self._config.entry(slot) for slot in Shortcut.slots()]
        separator = self.element.separator(context.width)
        compact = [Shortcut.compact_label(slot) for slot in Shortcut.slots()]
        for detailed in (True, False):
            labels = [display_text(item.display(detailed=detailed)) if item else label
                      for item, label in zip(slots, compact)]
            if width(separator.join(labels)) <= context.width:
                break
        else:
            labels = fit_labels(labels, context.width, separator,
                                fixed=tuple(index for index, item in enumerate(slots) if item is None))
        result = []
        for index, (item, label) in enumerate(zip(slots, labels)):
            if index:
                result.append((context.theme.paint(), separator, swallow_mouse))
            enabled = item is not None and item.enabled
            result.append((context.theme.literal(item.display_color if item else DockPalette.MUTED), label,
                           self._handler(item) if enabled else swallow_mouse))
        return tuple(result)
