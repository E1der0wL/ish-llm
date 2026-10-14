"""Page-region emphasis applied after child styles, before popup overlays."""

from collections.abc import Callable
from functools import partial

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.screen import Char


class FocusRegion(HSplit):
    def __init__(self, content, *, active: Callable[[], bool], on_click=None):
        super().__init__([content])
        self.active = active
        self.on_click = on_click

    @staticmethod
    def _tint(screen, area):
        # Resolve the active style before adding the inactive overlay. A default
        # terminal background stays transparent; actual colored surfaces turn gray.
        attrs = get_app().renderer._attrs_for_style
        styles, cells = {}, {}
        for y in range(area.ypos, area.ypos + area.height):
            row = screen.data_buffer[y]
            for x in range(area.xpos, area.xpos + area.width):
                cell = row[x]
                if cell.style not in styles:
                    original = attrs[cell.style]
                    surface = original.bgcolor not in (None, "", "default") or original.reverse
                    styles[cell.style] = cell.style + (
                        " class:hub.inactive-surface" if surface else " class:hub.inactive") + (
                        " bold" if original.bold else "")
                key = (cell.char, cell.style)
                if key not in cells:
                    cells[key] = Char(cell.char, styles[cell.style])
                row[x] = cells[key]

    def write_to_screen(self, screen, mouse_handlers, write_position,
                        parent_style, erase_bg, z_index):
        mouse = getattr(get_app(), "hub_mouse", None)
        if mouse is not None and self.on_click is not None:
            mouse.regions.append((self, write_position))
        super().write_to_screen(screen, mouse_handlers, write_position,
                                parent_style, erase_bg, z_index)
        if not self.active():
            # Child windows may be deferred at this z-index. Append the tint
            # after them, while higher popup/menus keep their own palette.
            # Only visible cells are visited; identical styles/cells share work.
            screen.draw_with_z_index(z_index or 0, partial(self._tint, screen, write_position))
