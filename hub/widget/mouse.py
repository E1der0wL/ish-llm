"""Hub mouse dispatch, hover and scrollbar capture; never grants focusability."""

from copy import copy
from dataclasses import dataclass
from collections.abc import Callable

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEventType as M
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.utils import get_cwidth


@dataclass(slots=True)
class MouseCapture:
    target: object
    update: Callable
    position: Point

    def move(self, position, finished=False):
        self.position = position
        self.update(position, finished)

    def finish(self):
        self.move(self.position, True)


@dataclass(frozen=True, slots=True)
class ScrollbarGeometry:
    height: int
    total: int
    visible: int
    offset: int
    arrows: bool = True

    @property
    def edge(self):
        return int(self.arrows and self.height >= 3)

    @property
    def track(self):
        return max(1, self.height - 2 * self.edge)

    @property
    def limit(self):
        return max(0, self.total - self.visible)

    @property
    def thumb(self):
        return min(self.track, max(1, round(self.track * self.visible / max(1, self.total))))

    @property
    def top(self):
        return self.edge + (round((self.track - self.thumb) * min(self.limit, self.offset) / self.limit)
                            if self.limit else 0)

    def position(self, row, grab=0):
        travel = self.track - self.thumb
        return round(max(0, min(travel, row - self.edge - grab)) * self.limit / travel) if travel else 0


class WindowScroll:
    """Independent viewport scrolling, including PTK lists and text controls."""

    def __init__(self, window):
        self.window = window
        self.manual = None
        self.cursor = self.anchor = None
        self._native_scroll = window._scroll
        # Local adapter only: PTK classes and the host's windows are unchanged.
        window._scroll = self._scroll

    def _scroll(self, content, width, height):
        cursor = content.cursor_position
        self.cursor = cursor
        if self.manual is not None:
            if cursor != self.anchor:
                self.manual = None
            else:
                self.manual = max(0, min(max(0, content.line_count - height), self.manual))
                content = copy(content)
                content.cursor_position = Point(cursor.x, max(self.manual, min(self.manual + height - 1, cursor.y)))
                self.window.vertical_scroll = self.manual
        self._native_scroll(content, width, height)

    def geometry(self, height=None, arrows=True):
        info = self.window.render_info
        return ScrollbarGeometry(height or info.window_height, info.content_height,
                                 info.window_height, self.window.vertical_scroll, arrows)

    def scroll_to(self, position):
        position = max(0, min(self.geometry().limit, position))
        callback = getattr(self.window.content, "scroll_to", None)
        if callback is not None:
            callback(position)
        else:
            self.manual = position
            # Capture the current logical cursor before the next repaint.
            self.anchor = self.cursor or self.window.render_info.ui_content.cursor_position
            self.window.vertical_scroll = position


def window_scroll(window):
    binding = getattr(window, "hub_mouse_scroll", None)
    if binding is None:
        binding = window.hub_mouse_scroll = WindowScroll(window)
    return binding


def hover_style(window):
    original = window.style
    window.style = lambda: (original() if callable(original) else original) + (
        " class:hub.mouse-hover" if getattr(window, "hub_mouse_hover", False) else "")


def contains(rect, point):
    return rect.xpos <= point.x < rect.xpos + rect.width and rect.ypos <= point.y < rect.ypos + rect.height


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
    def __init__(self, view):
        self.view = view
        self.app = None
        self.windows = []
        self.panes = []
        self.regions = []
        self._pane_layers = {}
        self.hovered = None
        self.drag = None
        self._cancelled_drag = False

    def attach(self, app):
        if self.app is app:
            return
        self.detach()
        self.app = app
        app.before_render += self._before_render
        app.after_render += self._after_render
        app.hub_mouse = self

    def detach(self):
        if self.app is None:
            return
        if self.drag:
            self.drag.finish()
        self._hover(None)
        app = self.app
        app.before_render -= self._before_render
        app.after_render -= self._after_render
        grid = app.renderer.mouse_handlers.mouse_handlers
        if isinstance(grid, _MouseGrid) and grid.owner is self:
            app.renderer.mouse_handlers.mouse_handlers = grid.rows
        if getattr(app, "hub_mouse", None) is self:
            del app.hub_mouse
        self.app, self.drag = None, None
        self._cancelled_drag = False
        self.windows, self.panes = [], []
        self.regions = []
        self._pane_layers = {}

    def _before_render(self, app):
        if not self.view.mouse_enabled:
            if self.drag:
                self.drag.finish()
                self.drag = None
            self._hover(None)
            self._cancelled_drag = False
        self.panes = []
        self.regions = []
        self._pane_layers = {}

    def _after_render(self, app):
        if app.is_done:
            self.detach()
            return
        screen = app.renderer._last_screen
        if not self.view.visible or screen is None:
            return
        self.windows = list(screen.visible_windows_to_write_positions.items())
        if self.hovered is not None and not self._present(self.hovered):
            self._hover(None)
        if self.drag and not self._present(self.drag.target):
            self.drag.finish()
            self.drag = None
            self._cancelled_drag = True
        grid = app.renderer.mouse_handlers.mouse_handlers
        if not isinstance(grid, _MouseGrid):
            app.renderer.mouse_handlers.mouse_handlers = _MouseGrid(self, grid)

    def _present(self, target):
        modal = self._scope()
        return (target in modal and any(window is target for window, _ in self.windows)
                or any(pane is target for pane, _ in self.panes) and target in modal)

    def _scope(self):
        dialog = getattr(self.view, "_dialog", None)
        if dialog is None and self.view.output.modal_container is not None:
            return set(walk(to_container(self.view.output.modal_container)))
        # A zero-height modal key receiver must not block its read-only body.
        return set(walk(to_container(dialog))) if dialog is not None else set(self.app.layout.walk_through_modal_area())

    def record_pane(self, pane, position, screen):
        self.panes.append((pane, position))
        self._pane_layers[pane] = next(reversed(screen.visible_windows_to_write_positions), None)

    def _hover(self, target):
        if self.hovered is target:
            return False
        if self.hovered is not None:
            self.hovered.hub_mouse_hover = False
        self.hovered = target
        if target is not None:
            target.hub_mouse_hover = True
        if self.app is not None:
            self.app.invalidate()
        return True

    def _hit(self, point):
        for window, rect in reversed(self.windows):
            clip = getattr(window, "hub_mouse_clip", None)
            if not any(pane is getattr(window, "hub_mouse_pane", None) for pane, _ in self.panes):
                clip = None
            if contains(rect, point) and (clip is None or contains(clip, point)):
                return window, rect
        return None, None

    def _bar(self, window, rect, point):
        offset = rect.xpos + rect.width
        for margin in reversed(window.right_margins):
            width = window._get_margin_width(margin)
            offset -= width
            if getattr(margin, "hub_interactive", False) and offset <= point.x < offset + width:
                return window_scroll(window), rect, margin.display_arrows()
        return None

    def _drag_position(self, target, grab, arrows, point):
        for pane, rect in self.panes:
            if target is pane:
                pane.scroll_to(pane.geometry(arrows=arrows).position(point.y - rect.ypos, grab))
                return
        for window, rect in self.windows:
            if target is window:
                binding = window_scroll(window)
                binding.scroll_to(binding.geometry(rect.height, arrows).position(point.y - rect.ypos, grab))
                return

    def _selection_point(self, window, point, *, autoscroll=False):
        rect = next(rect for item, rect in self.windows if item is window)
        binding = window_scroll(window)
        if autoscroll and not rect.ypos <= point.y < rect.ypos + rect.height:
            binding.scroll_to(binding.geometry().offset + (-1 if point.y < rect.ypos else 1))
        content = window.content
        top = getattr(content, "top_line", window.vertical_scroll)
        row = min(content.selection.content.line_count - 1,
                  top + max(0, min(rect.height - 1, point.y - rect.ypos)))
        margins = sum(window._get_margin_width(margin) for margin in window.left_margins)
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
            other = getattr(item.content, "selection", None)
            if other is not None and other is not selection:
                other.clear()
        binding = window_scroll(window)
        binding.scroll_to(binding.geometry().offset)
        selection.begin(self._selection_point(window, point))
        select_cell = getattr(window.content, "select_point", None)
        if select_cell:
            select_cell(selection.anchor)

        def update(position, finished=False):
            # Coordinates can disappear during a resize/modal switch.
            if any(item is window for item, _ in self.windows):
                selection.extend(self._selection_point(window, position, autoscroll=not finished))

        self.drag = MouseCapture(window, update, point)

    def _select_input(self, window, event, native):
        """Capture a writable input drag while retaining PTK's display mapping."""
        native(event)
        def update(position, finished=False):
            if not self._present(window) or window.content.buffer.read_only():
                return
            rect = next(rect for item, rect in self.windows if item is window)
            clip = getattr(window, "hub_mouse_clip", None) or rect
            top, bottom = max(rect.ypos, clip.ypos), min(rect.ypos + rect.height, clip.ypos + clip.height) - 1
            left = rect.xpos + sum(window._get_margin_width(m) for m in window.left_margins)
            right = rect.xpos + rect.width - sum(window._get_margin_width(m) for m in window.right_margins) - 1
            point = Point(max(left, min(right, position.x)), max(top, min(bottom, position.y)))
            grid = self.app.renderer.mouse_handlers.mouse_handlers
            rows = grid.rows if isinstance(grid, _MouseGrid) else grid
            from prompt_toolkit.mouse_events import MouseEvent
            rows[point.y][point.x](MouseEvent(point, M.MOUSE_UP if finished else M.MOUSE_MOVE,
                                            MouseButton.LEFT, event.modifiers))
            if not finished and (position.y < top or position.y > bottom):
                buffer = window.content.buffer
                buffer.cursor_up() if position.y < top else buffer.cursor_down()
        self.drag = MouseCapture(window, update, event.position)

    def dispatch(self, event, native):
        if not self.view.visible:
            return native(event)
        if not self.view.mouse_enabled:
            return None  # Discard a final report queued before mode changed.
        if self._cancelled_drag:
            if event.event_type == M.MOUSE_UP:
                self._cancelled_drag = False
                return None
            if event.event_type == M.MOUSE_DOWN or event.button == MouseButton.NONE:
                self._cancelled_drag = False
            else:
                return None
        if self.drag:
            if not self._present(self.drag.target):
                self.drag.finish()
                self.drag = None
                self._cancelled_drag = event.event_type != M.MOUSE_UP
                return None
            elif event.event_type == M.MOUSE_UP:
                self.drag.move(event.position, True)
                self.drag = None
                self.app.invalidate()
                return None
            elif event.event_type == M.MOUSE_MOVE and event.button == MouseButton.LEFT:
                self.drag.move(event.position)
                self.app.invalidate()
                return None
            elif event.event_type == M.MOUSE_MOVE and event.button == MouseButton.NONE:
                self.drag.finish()
                self.drag = None  # The terminal may have lost a release outside its window.
        window, rect = self._hit(event.position)
        modal = self._scope()
        if event.event_type == M.MOUSE_DOWN and event.button == MouseButton.LEFT:
            for region, area in reversed(self.regions):
                if region in modal and contains(area, event.position) and not region.active():
                    region.on_click()
                    break
        layers = {item: index for index, (item, _) in enumerate(self.windows)}
        pane_bar = next(((pane, area) for pane, area in reversed(self.panes)
                         if pane in modal and contains(area, event.position)
                         and event.position.x == area.xpos + area.width - 1
                         and (window is None or layers.get(window, -1) <=
                              layers.get(self._pane_layers.get(pane), -1))), None)
        if window not in modal and pane_bar is None:
            self._hover(None)
            return None
        pane_hit = next(((pane, area) for pane, area in reversed(self.panes)
                         if pane in modal and contains(area, event.position)
                         and getattr(window, "hub_mouse_pane", None) is pane), None)
        # The pane's scrollbar has no native Window of its own.
        bar = (pane_bar[0], pane_bar[1], True) if pane_bar else self._bar(window, rect, event.position)
        target = pane_bar[0] if pane_bar else window
        moved = False
        if event.event_type in (M.MOUSE_MOVE, M.MOUSE_DOWN) and event.button in (MouseButton.NONE, MouseButton.LEFT):
            moved = self._hover(target)
            if not bar and window is not None and window.content.is_focusable() and event.event_type == M.MOUSE_DOWN:
                if self.app.layout.current_control is not window.content:
                    self.app.layout.focus(window)
                    self.app.invalidate()
                    moved = True
        if event.event_type in (M.SCROLL_UP, M.SCROLL_DOWN):
            binding = pane_bar[0] if pane_bar else pane_hit[0] if pane_hit else window_scroll(window)
            geometry = binding.geometry()
            binding.scroll_to(geometry.offset + (-3 if event.event_type == M.SCROLL_UP else 3))
            self.app.invalidate()
            return None
        if bar:
            binding, area, arrows = bar
            geometry = binding.geometry(area.height, arrows)
            if event.event_type == M.MOUSE_DOWN and event.button == MouseButton.LEFT:
                row = event.position.y - area.ypos
                if geometry.edge and row in (0, geometry.height - 1):
                    binding.scroll_to(geometry.offset + (-1 if row == 0 else 1))
                elif geometry.limit:
                    if geometry.top <= row < geometry.top + geometry.thumb:
                        grab = row - geometry.top
                        self.drag = MouseCapture(target, lambda point, finished=False:
                            self._drag_position(target, grab, arrows, point), event.position)
                    else:
                        binding.scroll_to(geometry.offset + (-geometry.visible if row < geometry.top else geometry.visible))
                self.app.invalidate()
            return None if moved or event.event_type != M.MOUSE_MOVE else NotImplemented
        if event.event_type == M.MOUSE_DOWN and event.button == MouseButton.LEFT:
            focus = getattr(window, "hub_click_focus", None)
            if focus is not None:
                focus()
                self.app.invalidate()
            if hasattr(window, "drag_handler"):
                self.drag = MouseCapture(window, window.drag_handler(event.position), event.position)
                return None
            if getattr(window.content, "selection", None) is not None:
                self._select(window, event.position)
                self.app.invalidate()
                return None
            if (getattr(window.content, "hub_editable", False)
                    and not window.content.buffer.read_only()
                    and self.app.layout.current_control is window.content):
                self._select_input(window, event, native)
                self.app.invalidate()
                return None
        result = native(event)
        return None if moved else result
