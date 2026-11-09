"""Session-list attention state and animation; never an execution state owner."""

import asyncio
from enum import StrEnum
from time import monotonic

from prompt_toolkit.application.current import get_app
from rich.spinner import Spinner

from llm.core.models import RunStatus, SessionStatus
from ..locales.keys import MessageKey
from ..model import HubSnapshot, SessionSummary
from .captions import Caption
from .sidebar import SidebarItem, SidebarList


class SessionListState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"


class SessionList(SidebarList):
    LABELS = {
        SessionListState.IDLE: MessageKey.SESSION_LIST_IDLE,
        SessionListState.RUNNING: MessageKey.SESSION_LIST_RUNNING,
        SessionListState.INPUT_REQUIRED: MessageKey.SESSION_LIST_INPUT_REQUIRED,
        SessionListState.COMPLETED: MessageKey.SESSION_LIST_COMPLETED,
    }
    SPINNER = "dots"
    SEPARATOR = " "
    MILLISECONDS_PER_SECOND = 1000

    def __init__(self, view):
        self.view = view
        self.observations = {}
        self.seen = {}
        self.spinner = Spinner(self.SPINNER)
        self._timer = None
        self._app = None
        self._revision = 0
        self._rows_key = None
        self._rows = []
        self._running_lines = set()
        self._heading_window = None
        self._heading_running = False
        self._frame_key = None
        self._frame = ""
        self._label_key = None
        self._labels = {}
        super().__init__(self.rows, lambda: view._draft_key(view.selected),
                         view._select_sidebar_session, on_activate=view._focus_composer)

    def observe(self, snapshot):
        self.observations = {item.id: item for item in snapshot.sessions}
        self._revision += 1
        for key in tuple(self.seen):
            if key[0] == snapshot.project_id and key[1] not in self.observations:
                del self.seen[key]

    def state(self, identifier):
        item = self.observations.get(identifier)
        if item is None:
            return SessionListState.IDLE
        if item.pending_input or item.status in SessionSummary.WAITING_STATES:
            return SessionListState.INPUT_REQUIRED
        if item.active or item.queued_count or item.status == SessionStatus.RUNNING:
            return SessionListState.RUNNING
        key = (HubSnapshot.project_identifier(self.view), identifier)
        if item.latest_run_status == RunStatus.COMPLETED and item.latest_run_id:
            return (SessionListState.IDLE if self.seen.get(key) == item.latest_run_id
                    else SessionListState.COMPLETED)
        return SessionListState.IDLE

    def mark_viewed(self):
        view = self.view
        if not view.visible or view.settings_open or view._dialog is not None or view.no_sessions:
            return
        identifier = view.sessions[view.selected].id
        item = self.observations.get(identifier)
        if item and self.state(identifier) == SessionListState.COMPLETED:
            self.seen[HubSnapshot.project_identifier(view), identifier] = item.latest_run_id
            self._revision += 1

    @property
    def interval(self):
        return max(self.view.theme.progress_refresh_interval,
                   self.spinner.interval / self.MILLISECONDS_PER_SECOND)

    def _active(self):
        return self.view.visible and not self.view.settings_open and self.view._dialog is None

    def rows(self):
        self.mark_viewed()
        view, app = self.view, get_app()
        now = monotonic()
        frame_key = int(now / self.interval)
        label_key = (view.t, view.theme.icon_style)
        if label_key != self._label_key:
            self._label_key = label_key
            self._labels = {state: view.t(label) for state, label in self.LABELS.items()}
        # PTK asks for fragments, cursor position and preferred dimensions more
        # than once in a frame. Keep one projection; it contains no backend reads.
        key = (app, app.render_counter, id(view.sessions), view.selected,
               HubSnapshot.project_identifier(view), self._revision, label_key, frame_key,
               view.no_sessions)
        if key == self._rows_key:
            return self._rows
        result = []
        self._running_lines = set()
        if not view.no_sessions:
            for index, session in enumerate(view.sessions):
                state = self.state(session.id)
                if state == SessionListState.RUNNING:
                    if self._frame_key != frame_key:
                        self._frame_key = frame_key
                        self._frame = self.spinner.render(now).plain
                    glyph = self._frame
                    self._running_lines.add(index * self.DETAIL_HEIGHT + self.ROW_HEIGHT)
                else:
                    glyph = (view.icons.question if state == SessionListState.INPUT_REQUIRED
                             else view.icons.session_completed if state == SessionListState.COMPLETED
                             else view.icons.session_idle)
                detail = (glyph + self.SEPARATOR if glyph else "") + self._labels[state]
                observation = self.observations.get(session.id)
                detail = view.t(MessageKey.QUEUED, status=detail,
                                count=observation.queued_count if observation else 0)
                result.append(SidebarItem(view._draft_key(index), session.title, detail))
        self._rows_key, self._rows = key, result
        return result

    def heading(self, window):
        """Use the same cached state and spinner frame in the Home output header."""
        rows = self.rows()
        self._heading_window = window
        self._heading_running = bool(rows and self.state(
            self.view.sessions[self.view.selected].id) == SessionListState.RUNNING)
        if self._active():
            self._observe_render()
        if not rows:
            return ""
        row = rows[self.view.selected]
        return Caption(row.title, details=(row.detail,)).render()

    def _observe_render(self):
        app = get_app()
        if self._app is not app:
            self.close_animation()
            self._app = app
            app.after_render += self._after_render

    def create_content(self, width, height):
        content = super().create_content(width, height)
        # Measure calls do not start animations. Decide after painting, when PTK
        # exposes the actual visible source rows, including the scroll position.
        if height and self._active():
            self._observe_render()
        return content

    def _after_render(self, app):
        visible = app.layout.visible_windows
        heading_visible = self._heading_running and self._heading_window in visible
        rows_visible = self._running_lines and any(
            window.content is self and window.render_info is not None
            and any(row in self._running_lines
                    for row, _ in window.render_info.visible_line_to_row_col.values())
            for window in visible
        )
        animate = self._active() and (heading_visible or rows_visible)
        if not animate:
            # Do not remove this handler while Event is dispatching its list:
            # doing so would skip the next owner's after_render callback.
            self._cancel_timer()
        elif self._timer is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            def refresh():
                self._timer = None
                if self._active():
                    app.invalidate()
            self._timer = loop.call_later(self.interval, refresh)

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def close_animation(self):
        self._cancel_timer()
        if self._app is not None:
            self._app.after_render -= self._after_render
            self._app = None
