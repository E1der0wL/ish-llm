"""A full-size Float which paints only its bounded bottom strip."""

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.containers import Container
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.screen import WritePosition

from .view import DockGeometry, DockRow, DockStyle


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

    def __init__(self, controller, view):
        self._controller = controller
        self._view = view
        self._available = (0, 0)
        self._fragments = {row: () for row in DockRow}
        self._windows = {
            row: DockRowWindow(
                DockControl(lambda row=row: list(self._fragments[row]), focusable=False, show_cursor=False),
                height=1, wrap_lines=False, style=DockStyle.BACKGROUND,
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
        body_width = self._controller.config.width.resolve(self._controller.pty_columns(), width)
        return DockGeometry.fits(height, body_width)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        owner = self._controller
        self._available = (write_position.height, write_position.width)
        if not owner.visible or not self.has_room:
            return
        body_width = owner.config.width.resolve(owner.pty_columns(), write_position.width)
        lines = self._view.render(owner.config, owner.playback, owner.audio, owner.diagnostic,
                                  write_position.width, body_width)
        top = write_position.ypos + write_position.height - DockGeometry.HEIGHT
        for line in lines:
            if line.width:
                self._fragments[line.row] = line.fragments
                position = WritePosition(write_position.xpos + line.left, top + line.row, line.width, 1)
                self._windows[line.row].write_to_screen(
                    screen, mouse_handlers, position, parent_style, erase_bg, z_index,
                )
