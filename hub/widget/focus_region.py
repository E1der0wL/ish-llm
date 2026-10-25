"""Page-region emphasis applied after child styles, before popup overlays."""

from collections.abc import Callable
from functools import partial
from functools import lru_cache

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.output.vt100 import ANSI_COLORS_TO_RGB
from ..config.theme import HubTheme
from ..config.theme_style import ThemeSelector, ThemeStyle
from .mouse_contract import MouseBinding


class RegionTint:
    CACHE_SIZE = 1024
    ABSENT = None
    EMPTY = ""
    TERMINAL_DEFAULTS = (ABSENT, EMPTY, HubTheme.DEFAULT_COLOR)
    RGB_OFFSETS = tuple(offset - 1 for offset in HubTheme.HEX_CHANNEL_OFFSETS)
    LUMINANCE_WEIGHTS = tuple(weight for _, weight in HubTheme.LUMINANCE_WEIGHTS)
    GRAY_FORMAT = HubTheme.HEX_PREFIX + HubTheme.HEX_CHANNEL_FORMAT * 3
    COLORS = " fg:{foreground} bg:{background}"
    NEGATED_ATTRIBUTE = ThemeStyle.NEGATED_ATTRIBUTE
    ATTRIBUTES = ThemeStyle.ATTRIBUTES


@lru_cache(maxsize=RegionTint.CACHE_SIZE)
def grayscale(color):
    """Desaturate resolved RGB/ANSI colors while preserving terminal defaults."""
    if color in RegionTint.TERMINAL_DEFAULTS:
        return HubTheme.DEFAULT_COLOR
    rgb = ANSI_COLORS_TO_RGB.get(color)
    if rgb is RegionTint.ABSENT:
        try:
            rgb = tuple(
                int(color[i : i + HubTheme.HEX_CHANNEL_WIDTH], HubTheme.HEX_RADIX)
                for i in RegionTint.RGB_OFFSETS
            )
        except ValueError:
            return HubTheme.DEFAULT_COLOR
    value = round(
        sum(
            channel * weight
            for channel, weight in zip(rgb, RegionTint.LUMINANCE_WEIGHTS)
        )
    )
    return RegionTint.GRAY_FORMAT.format(value=value)


class FocusRegion(HSplit):
    ABSENT = None
    TINT = True
    BASE_LAYER = 0

    def __init__(
        self, content, *, active: Callable[[], bool], on_click=ABSENT, tint=TINT
    ):
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
                    styles[cell.style] = (
                        cell.style
                        + ThemeStyle.MODIFIER_SEPARATOR
                        + ThemeSelector.INACTIVE.style
                        + RegionTint.COLORS.format(
                            foreground=grayscale(original.color),
                            background=grayscale(original.bgcolor),
                        )
                        + RegionTint.EMPTY.join(
                            ThemeStyle.MODIFIER_SEPARATOR
                            + (
                                name
                                if getattr(original, name)
                                else RegionTint.NEGATED_ATTRIBUTE + name
                            )
                            for name in RegionTint.ATTRIBUTES
                        )
                    )
                key = (cell.char, cell.style)
                if key not in cells:
                    cells[key] = Char(cell.char, styles[cell.style])
                row[x] = cells[key]

    def write_to_screen(
        self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
    ):
        mouse = MouseBinding.controller(get_app())
        if mouse is not self.ABSENT and self.on_click is not self.ABSENT:
            mouse.regions.append((self, write_position))
        super().write_to_screen(
            screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
        )
        if self.tint and not self.active():
            # Child windows may be deferred at this z-index. Append the tint
            # after them, while higher popup/menus keep their own palette.
            # Only visible cells are visited; identical styles/cells share work.
            screen.draw_with_z_index(
                z_index or self.BASE_LAYER, partial(self._tint, screen, write_position)
            )
