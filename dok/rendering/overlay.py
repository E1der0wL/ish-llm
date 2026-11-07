"""A full-size Float which paints only its bounded bottom strip."""

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.containers import Container
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.screen import WritePosition

from ..core.state import DockMode
from ..dashboard.layout import DockGeometry
from ..dashboard.view import DashboardView
from ..dashboard.widgets import DockRow
from ..locales import Locale


class OverlayLayers(Container):
    """Keep one host Float while painting independent plugin-owned layers."""

    def __init__(self, *layers):
        self.layers = layers

    def reset(self):
        for layer in self.layers:
            layer.reset()

    def preferred_width(self, max_available_width):
        return Dimension.exact(0)

    def preferred_height(self, width, max_available_height):
        return Dimension.exact(0)

    def get_children(self):
        return list(self.layers)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        for layer in self.layers:
            layer.write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)


class DockControl(FormattedTextControl):
    def mouse_handler(self, mouse_event):
        super().mouse_handler(mouse_event)
        # Visible dock content consumes clicks; unpainted areas retain host handlers.
        return None


class DockRowWindow(Window):
    """A fitted, non-focusable row has no scrolling or cursor-following work."""

    def _scroll(self, ui_content, width, height):
        # Window's normal scrolling caches the full current line's cell width.
        # DockView already fits each row to its exact paint width. Avoid that
        # unbounded cache without altering prompt-toolkit or the host's windows.
        self.vertical_scroll = self.vertical_scroll_2 = self.horizontal_scroll = 0


class DockOverlay(Container):
    TRANSPARENT = True

    def __init__(self, controller, view, *, locale=Locale):
        self._controller = controller
        self._view = view
        self._locale = locale
        self._available = (0, 0)
        self._dashboard = DashboardView()
        self.display_mode = DockMode.HIDDEN
        self.mouse_regions = []
        self._fragments = {row: () for row in DockRow}
        self._windows = {
            row: DockRowWindow(
                DockControl(lambda row=row: list(self._fragments[row]), focusable=False, show_cursor=False),
                height=1, wrap_lines=False, style=lambda: self._controller.config.theme.paint(),
            ) for row in DockRow
        }

    def reset(self):
        for window in self._windows.values():
            window.reset()

    def preferred_width(self, max_available_width):
        return Dimension.exact(0)

    def preferred_height(self, width, max_available_height):
        return Dimension.exact(0)

    def get_children(self):
        return list(self._windows.values())

    @property
    def has_room(self) -> bool:
        height, width = self._available
        mode, _, _ = self._layout(height, width)
        return mode != DockMode.HIDDEN

    def _layout(self, height, width):
        owner = self._controller
        body_width = owner.config.dock.width.resolve(owner.pty_columns(), width)
        extra = (self._dashboard.rows(owner.config, owner.weather, body_width, max_rows=height, locale=self._locale())
                 if owner.requested_mode == DockMode.MAXIMIZED else ())
        if extra is None:
            return DockGeometry.resolve(DockMode.BASIC, height, body_width, 0, owner.config), (), body_width
        mode = DockGeometry.resolve(owner.requested_mode, height, body_width, len(extra), owner.config)
        return mode, extra if mode == DockMode.MAXIMIZED else (), body_width

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        owner = self._controller
        self._available = (write_position.height, write_position.width)
        self.display_mode = DockMode.HIDDEN
        self.mouse_regions = []
        if not owner.visible:
            return
        self.display_mode, extra, body_width = self._layout(*self._available)
        if self.display_mode == DockMode.HIDDEN:
            return
        lines = self._view.render(owner.config, owner.playback, owner.audio, owner.diagnostic,
                                  write_position.width, body_width, extra, self.display_mode, locale=self._locale())
        top = write_position.ypos + write_position.height - DockGeometry.height(self.display_mode, len(extra), owner.config)
        for line in lines:
            if line.width:
                if line.row not in self._windows:
                    row = line.row
                    self._windows[row] = DockRowWindow(
                        DockControl(
                            lambda row=row: list(self._fragments[row]),
                            focusable=False,
                            show_cursor=False
                        ),
                        height=1, wrap_lines=False, style=lambda: self._controller.config.theme.paint(),
                    )
                self._fragments[line.row] = line.fragments
                position = WritePosition(write_position.xpos + line.left, top + line.row, line.width, 1)
                self.mouse_regions.append((position.xpos, position.xpos + line.width, position.ypos))
                self._windows[line.row].write_to_screen(
                    screen, mouse_handlers, position, parent_style, erase_bg, z_index,
                )
