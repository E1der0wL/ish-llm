"""Temporarily enable mouse reporting and restrict dispatch to painted dock rows."""
from prompt_toolkit.filters import Condition, to_filter
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.mouse_events import MouseEventType

from ...core.records import Boolean, Record, setting
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MouseConfig(Record):
    enabled: bool = setting(Boolean(), default=False,
        description="Enable dock-only mouse actions while visible. Terminal text selection requires Shift+drag.")


class _Columns:
    def __init__(self, dispatch):
        self._dispatch = dispatch

    def __getitem__(self, column):
        return self._dispatch


class _Rows:
    def __init__(self, dispatch):
        self._columns = _Columns(dispatch)

    def __getitem__(self, row):
        return self._columns


class DockMouse:
    def __init__(self, app, active, regions, cancel):
        self._app, self._active, self._regions, self._cancel = app, active, regions, cancel
        self._previous = app.mouse_support
        self._filter = Condition(active) | to_filter(self._previous)
        self._source = None
        self._scoped = None
        self._was_active = False
        app.mouse_support = self._filter

    def _dispatch(self, event):
        if not self._active():
            return None
        try:
            x, y = event.position.x, event.position.y
            if any(left <= x < right and y == row for left, right, row in self._regions()):
                return self._source.mouse_handlers[y][x](event)
            return None
        finally:
            if event.event_type == MouseEventType.MOUSE_UP:
                self._cancel()

    def sync(self):
        renderer = self._app.renderer
        active = self._active()
        if active != self._was_active:
            self._was_active = active
            self._app.invalidate()
        if renderer.mouse_handlers is self._scoped:
            renderer.mouse_handlers = self._source
        if active:
            self._source = renderer.mouse_handlers
            self._scoped = MouseHandlers()
            # Read-only lookup avoids allocating a dictionary per arbitrary coordinate.
            self._scoped.mouse_handlers = _Rows(self._dispatch)
            renderer.mouse_handlers = self._scoped
        else:
            self._cancel()

    def close(self):
        self._cancel()
        if self._app.mouse_support is self._filter:
            self._app.mouse_support = self._previous
        if self._app.renderer.mouse_handlers is self._scoped:
            self._app.renderer.mouse_handlers = self._source
