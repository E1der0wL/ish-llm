"""Hub mouse dispatch, hover and scrollbar capture; never grants focusability."""

from copy import copy
from dataclasses import dataclass
from typing import NamedTuple
from collections.abc import Callable

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEventType as M
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.utils import get_cwidth
from .input_contract import EditableControl, MouseResult
from .output.selection import SelectableControl
from ..config.theme_style import ThemeSelector, ThemeStyle
from .mouse_contract import MouseBinding


class MouseHit(NamedTuple):
    target: object
    area: object


class ScrollbarHit(NamedTuple):
    binding: object
    area: object
    arrows: bool


@dataclass(slots=True)
class MouseCapture:
    DRAGGING = False
    FINISHED = True

    target: object
    update: Callable
    position: Point

    def move(self, position, finished=DRAGGING):
        self.position = position
        self.update(position, finished)

    def finish(self):
        self.move(self.position, self.FINISHED)


@dataclass(frozen=True, slots=True)
class ScrollbarGeometry:
    ARROWS = True
    MIN_ARROW_HEIGHT = 3
    EDGES = 2
    MIN_TRACK = 1
    MIN_THUMB = 1
    ORIGIN = 0
    height: int
    total: int
    visible: int
    offset: int
    arrows: bool = ARROWS

    @property
    def edge(self):
        return int(self.arrows and self.height >= self.MIN_ARROW_HEIGHT)

    @property
    def track(self):
        return max(self.MIN_TRACK, self.height - self.EDGES * self.edge)

    @property
    def limit(self):
        return max(self.ORIGIN, self.total - self.visible)

    @property
    def thumb(self):
        return min(
            self.track,
            max(
                self.MIN_THUMB,
                round(self.track * self.visible / max(self.MIN_THUMB, self.total)),
            ),
        )

    @property
    def top(self):
        return self.edge + (
            round((self.track - self.thumb) * min(self.limit, self.offset) / self.limit)
            if self.limit
            else self.ORIGIN
        )

    def position(self, row, grab=ORIGIN):
        travel = self.track - self.thumb
        return (
            round(
                max(self.ORIGIN, min(travel, row - self.edge - grab))
                * self.limit
                / travel
            )
            if travel
            else self.ORIGIN
        )


class WindowScroll:
    """Independent viewport scrolling, including PTK lists and text controls."""

    ABSENT = None
    SCROLL_TO = "scroll_to"
    ORIGIN = 0

    def __init__(self, window):
        self.window = window
        self.manual = self.ABSENT
        self.cursor = self.anchor = self.ABSENT
        self._native_scroll = window._scroll
        # Local adapter only: PTK classes and the host's windows are unchanged.
        window._scroll = self._scroll

    def _scroll(self, content, width, height):
        cursor = content.cursor_position
        self.cursor = cursor
        if self.manual is not self.ABSENT:
            if cursor != self.anchor:
                self.manual = self.ABSENT
            else:
                self.manual = max(
                    self.ORIGIN,
                    min(max(self.ORIGIN, content.line_count - height), self.manual),
                )
                content = copy(content)
                content.cursor_position = Point(
                    cursor.x, max(self.manual, min(self.manual + height - 1, cursor.y))
                )
                self.window.vertical_scroll = self.manual
        self._native_scroll(content, width, height)

    def geometry(self, height=ABSENT, arrows=ScrollbarGeometry.ARROWS):
        info = self.window.render_info
        return ScrollbarGeometry(
            height or info.window_height,
            info.content_height,
            info.window_height,
            self.window.vertical_scroll,
            arrows,
        )

    def scroll_to(self, position):
        position = max(self.ORIGIN, min(self.geometry().limit, position))
        callback = getattr(self.window.content, self.SCROLL_TO, self.ABSENT)
        if callback is not self.ABSENT:
            callback(position)
        else:
            self.manual = position
            # Capture the current logical cursor before the next repaint.
            self.anchor = (
                self.cursor or self.window.render_info.ui_content.cursor_position
            )
            self.window.vertical_scroll = position


def window_scroll(window):
    binding = MouseBinding.scroll(window)
    if binding is MouseBinding.ABSENT:
        binding = WindowScroll(window)
        MouseBinding.set_scroll(window, binding)
    return binding


def hover_style(window):
    original = window.style
    window.style = lambda: (original() if callable(original) else original) + (
        ThemeStyle.MODIFIER_SEPARATOR + ThemeSelector.MOUSE_HOVER.style
        if MouseBinding.hovered(window)
        else MouseController.NO_STYLE
    )


def contains(rect, point):
    return (
        rect.xpos <= point.x < rect.xpos + rect.width
        and rect.ypos <= point.y < rect.ypos + rect.height
    )


class _MouseRow:
    def __init__(self, owner, row):
        self.owner, self.row = owner, row

    def __getitem__(self, x):
        handler = self.row[x]
        return lambda event: self.owner.dispatch(event, handler)


class _MouseGrid:
    """Wrap dispatch lazily, without allocating a callback for every screen cell."""

    def __init__(self, owner, rows):
        self.owner, self.rows = owner, rows

    def __getitem__(self, y):
        return _MouseRow(self.owner, self.rows[y])


class MouseController:
    ABSENT = None
    INACTIVE = False
    ACTIVE = True
    NO_STYLE = ""
    DIALOG = "_dialog"
    DRAG_HANDLER = "drag_handler"
    NO_LAYER = -1
    WHEEL_UP = -3
    WHEEL_DOWN = 3
    LINE_UP = -1
    LINE_DOWN = 1
    ORIGIN = 0

    def __init__(self, view):
        self.view = view
        self.app = self.ABSENT
        self.windows = []
        self.panes = []
        self.regions = []
        self._pane_layers = {}
        self.hovered = self.ABSENT
        self.drag = self.ABSENT
        self._cancelled_drag = self.INACTIVE

    def attach(self, app):
        if self.app is app:
            return
        self.detach()
        self.app = app
        app.before_render += self._before_render
        app.after_render += self._after_render
        MouseBinding.set_controller(app, self)

    def detach(self):
        if self.app is self.ABSENT:
            return
        if self.drag:
            self.drag.finish()
        self._hover(self.ABSENT)
        app = self.app
        app.before_render -= self._before_render
        app.after_render -= self._after_render
        grid = app.renderer.mouse_handlers.mouse_handlers
        if isinstance(grid, _MouseGrid) and grid.owner is self:
            app.renderer.mouse_handlers.mouse_handlers = grid.rows
        if MouseBinding.controller(app) is self:
            MouseBinding.remove_controller(app)
        self.app, self.drag = self.ABSENT, self.ABSENT
        self._cancelled_drag = self.INACTIVE
        self.windows, self.panes = [], []
        self.regions = []
        self._pane_layers = {}

    def _before_render(self, app):
        self.panes = []
        self.regions = []
        self._pane_layers = {}

    def _after_render(self, app):
        if app.is_done:
            self.detach()
            return
        screen = app.renderer._last_screen
        if not self.view.visible or screen is self.ABSENT:
            return
        self.windows = list(screen.visible_windows_to_write_positions.items())
        if self.hovered is not self.ABSENT and not self._present(self.hovered):
            self._hover(self.ABSENT)
        if self.drag and not self._present(self.drag.target):
            self.drag.finish()
            self.drag = self.ABSENT
            self._cancelled_drag = self.ACTIVE
        grid = app.renderer.mouse_handlers.mouse_handlers
        if not isinstance(grid, _MouseGrid):
            app.renderer.mouse_handlers.mouse_handlers = _MouseGrid(self, grid)

    def _present(self, target):
        modal = self._scope()
        return (
            target in modal
            and any(window is target for window, _ in self.windows)
            or any(pane is target for pane, _ in self.panes)
            and target in modal
        )

    def _scope(self):
        dialog = getattr(self.view, self.DIALOG, self.ABSENT)
        if (
            dialog is self.ABSENT
            and self.view.output.modal_container is not self.ABSENT
        ):
            return set(walk(to_container(self.view.output.modal_container)))
        # A zero-height modal key receiver must not block its read-only body.
        return (
            set(walk(to_container(dialog)))
            if dialog is not self.ABSENT
            else set(self.app.layout.walk_through_modal_area())
        )

    def record_pane(self, pane, position, screen):
        self.panes.append(MouseHit(pane, position))
        self._pane_layers[pane] = next(
            reversed(screen.visible_windows_to_write_positions), self.ABSENT
        )

    def _hover(self, target):
        if self.hovered is target:
            return self.INACTIVE
        if self.hovered is not self.ABSENT:
            MouseBinding.set_hovered(self.hovered, self.INACTIVE)
        self.hovered = target
        if target is not self.ABSENT:
            MouseBinding.set_hovered(target, self.ACTIVE)
        if self.app is not self.ABSENT:
            self.app.invalidate()
        return self.ACTIVE

    def _hit(self, point):
        for window, rect in reversed(self.windows):
            clip = MouseBinding.clip(window)
            if not any(pane is MouseBinding.pane(window) for pane, _ in self.panes):
                clip = self.ABSENT
            if contains(rect, point) and (clip is self.ABSENT or contains(clip, point)):
                return MouseHit(window, rect)
        return MouseHit(self.ABSENT, self.ABSENT)

    def _bar(self, window, rect, point):
        offset = rect.xpos + rect.width
        for margin in reversed(window.right_margins):
            width = window._get_margin_width(margin)
            offset -= width
            if MouseBinding.interactive(margin) and offset <= point.x < offset + width:
                return ScrollbarHit(
                    window_scroll(window), rect, margin.display_arrows()
                )
        return self.ABSENT

    def _drag_position(self, target, grab, arrows, point):
        for pane, rect in self.panes:
            if target is pane:
                pane.scroll_to(
                    pane.geometry(arrows=arrows).position(point.y - rect.ypos, grab)
                )
                return
        for window, rect in self.windows:
            if target is window:
                binding = window_scroll(window)
                binding.scroll_to(
                    binding.geometry(rect.height, arrows).position(
                        point.y - rect.ypos, grab
                    )
                )
                return

    def _selection_point(self, window, point, *, autoscroll=INACTIVE):
        rect = next(rect for item, rect in self.windows if item is window)
        binding = window_scroll(window)
        if autoscroll and not rect.ypos <= point.y < rect.ypos + rect.height:
            binding.scroll_to(
                binding.geometry().offset
                + (self.LINE_UP if point.y < rect.ypos else self.LINE_DOWN)
            )
        content = window.content
        top = SelectableControl.top_line_of(content, window.vertical_scroll)
        row = min(
            content.selection.content.line_count - 1,
            top + max(0, min(rect.height - 1, point.y - rect.ypos)),
        )
        margins = sum(
            window._get_margin_width(margin) for margin in window.left_margins
        )
        cell = max(0, point.x - rect.xpos - margins + window.horizontal_scroll)
        used = column = 0
        for char in content.selection.line(row):
            size = get_cwidth(char)
            if size and used + size > cell:
                break
            used += size
            column += 1
        return Point(column, row)

    def _select(self, window, point):
        selection = window.content.selection
        for item, _ in self.windows:
            other = SelectableControl.selection_of(item.content)
            if other is not self.ABSENT and other is not selection:
                other.clear()
        binding = window_scroll(window)
        binding.scroll_to(binding.geometry().offset)
        selection.begin(self._selection_point(window, point))
        select_cell = SelectableControl.point_selector(window.content)
        if select_cell:
            select_cell(selection.anchor)

        def update(position, finished=MouseCapture.DRAGGING):
            # Coordinates can disappear during a resize/modal switch.
            if any(item is window for item, _ in self.windows):
                selection.extend(
                    self._selection_point(window, position, autoscroll=not finished)
                )

        self.drag = MouseCapture(window, update, point)

    def _select_input(self, window, event, native):
        """Capture a writable input drag while retaining PTK's display mapping."""
        native(event)

        def update(position, finished=MouseCapture.DRAGGING):
            if not self._present(window) or window.content.buffer.read_only():
                return
            rect = next(rect for item, rect in self.windows if item is window)
            clip = MouseBinding.clip(window) or rect
            top, bottom = (
                max(rect.ypos, clip.ypos),
                min(rect.ypos + rect.height, clip.ypos + clip.height) - 1,
            )
            left = rect.xpos + sum(
                window._get_margin_width(m) for m in window.left_margins
            )
            right = (
                rect.xpos
                + rect.width
                - sum(window._get_margin_width(m) for m in window.right_margins)
                - 1
            )
            point = Point(
                max(left, min(right, position.x)), max(top, min(bottom, position.y))
            )
            grid = self.app.renderer.mouse_handlers.mouse_handlers
            rows = grid.rows if isinstance(grid, _MouseGrid) else grid
            from prompt_toolkit.mouse_events import MouseEvent

            rows[point.y][point.x](
                MouseEvent(
                    point,
                    M.MOUSE_UP if finished else M.MOUSE_MOVE,
                    MouseButton.LEFT,
                    event.modifiers,
                )
            )
            if not finished and (position.y < top or position.y > bottom):
                buffer = window.content.buffer
                buffer.cursor_up() if position.y < top else buffer.cursor_down()

        self.drag = MouseCapture(window, update, event.position)

    def dispatch(self, event, native):
        if not self.view.visible:
            return native(event)
        if self._cancelled_drag:
            if event.event_type == M.MOUSE_UP:
                self._cancelled_drag = self.INACTIVE
                return MouseResult.HANDLED
            if event.event_type == M.MOUSE_DOWN or event.button == MouseButton.NONE:
                self._cancelled_drag = self.INACTIVE
            else:
                return MouseResult.HANDLED
        if self.drag:
            if not self._present(self.drag.target):
                self.drag.finish()
                self.drag = self.ABSENT
                self._cancelled_drag = event.event_type != M.MOUSE_UP
                return MouseResult.HANDLED
            elif event.event_type == M.MOUSE_UP:
                self.drag.move(event.position, MouseCapture.FINISHED)
                self.drag = self.ABSENT
                self.app.invalidate()
                return MouseResult.HANDLED
            elif event.event_type == M.MOUSE_MOVE and event.button == MouseButton.LEFT:
                self.drag.move(event.position)
                self.app.invalidate()
                return MouseResult.HANDLED
            elif event.event_type == M.MOUSE_MOVE and event.button == MouseButton.NONE:
                self.drag.finish()
                self.drag = (
                    self.ABSENT
                )  # The terminal may have lost a release outside its window.
        window, rect = self._hit(event.position)
        pressed_control = self.app.layout.current_control
        modal = self._scope()
        if event.event_type == M.MOUSE_DOWN and event.button == MouseButton.LEFT:
            for region, area in reversed(self.regions):
                if (
                    region in modal
                    and contains(area, event.position)
                    and not region.active()
                ):
                    region.on_click()
                    break
        layers = {item: index for index, (item, _) in enumerate(self.windows)}
        pane_bar = next(
            (
                MouseHit(pane, area)
                for pane, area in reversed(self.panes)
                if pane in modal
                and contains(area, event.position)
                and event.position.x == area.xpos + area.width - 1
                and (
                    window is self.ABSENT
                    or layers.get(window, self.NO_LAYER)
                    <= layers.get(self._pane_layers.get(pane), self.NO_LAYER)
                )
            ),
            self.ABSENT,
        )
        if window not in modal and pane_bar is self.ABSENT:
            self._hover(self.ABSENT)
            return MouseResult.HANDLED
        pane_hit = next(
            (
                MouseHit(pane, area)
                for pane, area in reversed(self.panes)
                if pane in modal
                and contains(area, event.position)
                and MouseBinding.pane(window) is pane
            ),
            self.ABSENT,
        )
        # The pane's scrollbar has no native Window of its own.
        bar = (
            ScrollbarHit(pane_bar.target, pane_bar.area, ScrollbarGeometry.ARROWS)
            if pane_bar
            else self._bar(window, rect, event.position)
        )
        target = pane_bar.target if pane_bar else window
        moved = self.INACTIVE
        from .sidebar import SidebarList

        if (window is not self.ABSENT and isinstance(window.content, SidebarList)
                and event.event_type == M.MOUSE_DOWN and event.button == MouseButton.LEFT):
            window.content.begin_click(pressed_control is window.content)
        if event.event_type in (M.MOUSE_MOVE, M.MOUSE_DOWN) and event.button in (
            MouseButton.NONE,
            MouseButton.LEFT,
        ):
            moved = self._hover(target)
            if (
                not bar
                and window is not self.ABSENT
                and window.content.is_focusable()
                and event.event_type == M.MOUSE_DOWN
            ):
                if self.app.layout.current_control is not window.content:
                    self.app.layout.focus(window)
                    self.app.invalidate()
                    moved = self.ACTIVE
        if event.event_type in (M.SCROLL_UP, M.SCROLL_DOWN):
            binding = (
                pane_bar.target
                if pane_bar
                else pane_hit.target if pane_hit else window_scroll(window)
            )
            geometry = binding.geometry()
            binding.scroll_to(
                geometry.offset
                + (
                    self.WHEEL_UP
                    if event.event_type == M.SCROLL_UP
                    else self.WHEEL_DOWN
                )
            )
            self.app.invalidate()
            return MouseResult.HANDLED
        if bar:
            binding, area, arrows = bar
            geometry = binding.geometry(area.height, arrows)
            if event.event_type == M.MOUSE_DOWN and event.button == MouseButton.LEFT:
                row = event.position.y - area.ypos
                if geometry.edge and row in (self.ORIGIN, geometry.height - 1):
                    binding.scroll_to(
                        geometry.offset
                        + (self.LINE_UP if row == self.ORIGIN else self.LINE_DOWN)
                    )
                elif geometry.limit:
                    if geometry.top <= row < geometry.top + geometry.thumb:
                        grab = row - geometry.top
                        self.drag = MouseCapture(
                            target,
                            lambda point, finished=MouseCapture.DRAGGING: self._drag_position(
                                target, grab, arrows, point
                            ),
                            event.position,
                        )
                    else:
                        binding.scroll_to(
                            geometry.offset
                            + (
                                -geometry.visible
                                if row < geometry.top
                                else geometry.visible
                            )
                        )
                self.app.invalidate()
            return (
                MouseResult.HANDLED
                if moved or event.event_type != M.MOUSE_MOVE
                else MouseResult.UNHANDLED
            )
        if event.event_type == M.MOUSE_DOWN and event.button == MouseButton.LEFT:
            focus = MouseBinding.focus(window)
            if focus is not self.ABSENT:
                focus()
                self.app.invalidate()
            if hasattr(window, self.DRAG_HANDLER):
                self.drag = MouseCapture(
                    window, window.drag_handler(event.position), event.position
                )
                return MouseResult.HANDLED
            if SelectableControl.selection_of(window.content) is not self.ABSENT:
                self._select(window, event.position)
                self.app.invalidate()
                return MouseResult.HANDLED
            if (
                EditableControl.writable(window.content)
                and self.app.layout.current_control is window.content
            ):
                self._select_input(window, event, native)
                self.app.invalidate()
                return MouseResult.HANDLED
        if (
            window is not self.ABSENT
            and isinstance(window.content, SidebarList)
            and event.button == MouseButton.LEFT
            and event.event_type in (M.MOUSE_DOWN, M.MOUSE_UP)
            and event.position.y - rect.ypos + window.vertical_scroll
            >= window.render_info.content_height
        ):
            # PTK maps blank display rows to the last content row.
            self.app.layout.focus(window)
            self.app.invalidate()
            return MouseResult.HANDLED
        result = native(event)
        return MouseResult.HANDLED if moved else result
