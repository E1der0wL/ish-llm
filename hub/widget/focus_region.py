"""Page-region emphasis applied after child styles, before popup overlays."""

from collections.abc import Callable
from functools import partial
from functools import lru_cache

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.output.vt100 import ANSI_COLORS_TO_RGB


@lru_cache(maxsize=1024)
def grayscale(color):
    """Desaturate resolved RGB/ANSI colors while preserving terminal defaults."""
    if color in (None, "", "default"):
        return "default"
    rgb = ANSI_COLORS_TO_RGB.get(color)
    if rgb is None:
        try:
            rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return "default"
    value = round(sum(channel * weight for channel, weight in zip(rgb, (0.2126, 0.7152, 0.0722))))
    return f"#{value:02x}{value:02x}{value:02x}"


class FocusRegion(HSplit):
    def __init__(self, content, *, active: Callable[[], bool], on_click=None, tint=True):
        super().__init__([content])
        self.active = active
        self.on_click = on_click
        self.tint = tint

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
                    styles[cell.style] = (cell.style + " class:hub.inactive"
                        + f" fg:{grayscale(original.color)} bg:{grayscale(original.bgcolor)}"
                        + "".join(" " + (name if getattr(original, name) else "no" + name)
                                  for name in ("bold", "italic", "underline", "strike", "reverse", "blink", "hidden")))
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
        if self.tint and not self.active():
            # Child windows may be deferred at this z-index. Append the tint
            # after them, while higher popup/menus keep their own palette.
            # Only visible cells are visited; identical styles/cells share work.
            screen.draw_with_z_index(z_index or 0, partial(self._tint, screen, write_position))
