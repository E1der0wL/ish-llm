"""Typed, non-focusable alerts shared by Hub and the host shell."""

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import ClassVar, NamedTuple
from time import monotonic
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame
from rich.cells import chop_cells

from ..summary import clipped_summary
from llm.core.models import RunStatus
from ...model import SessionSummary
from ...config.notifications import NotificationKind
from ...config.theme_style import ThemeStyle


class SessionAlertKey(NamedTuple):
    """Observation of the opaque alert key when it denotes a Project/Session pair."""

    project_id: object
    session_id: object

    NO_SESSION = None

    @classmethod
    def read(cls, value):
        return (
            cls(*value)
            if isinstance(value, tuple) and len(value) == len(cls._fields)
            else cls.NO_SESSION
        )


@dataclass(frozen=True, slots=True)
class Alert:
    NO_STATUS: ClassVar[str] = ""

    identity: int
    key: object
    title: str
    text: str
    level: str
    expires: float
    status: str = NO_STATUS

    def removed_waiting_session(self, project_id, remaining):
        if self.status not in SessionSummary.WAITING_STATES:
            return False
        key = SessionAlertKey.read(self.key)
        return (
            key is not SessionAlertKey.NO_SESSION
            and key.project_id == project_id
            and key.session_id not in remaining
        )


class ToastLayout(NamedTuple):
    container: object
    height: int


class ToastLayoutKey(NamedTuple):
    width: int
    available: int
    items: tuple
    language: object
    icons: object


class SessionToasts:
    MAX_ITEMS = 3
    INITIAL_SERIAL = 0
    NO_APP = None
    NO_TIMER = None
    UNCOMPUTED = None
    AUTO_KEY = None
    EMPTY_HEIGHT = 1
    MIN_WIDTH = 1
    MAX_WIDTH = 48
    MARGIN_COLUMNS = 4
    RESERVED_ROWS = 3
    MIN_CONTENT_WIDTH = 8
    MIN_CONTENT_ROWS = 3
    BORDER_ROWS = 2
    NON_TEXT_COLUMNS = 6
    ICON_COLUMNS = 2
    PADDING_COLUMNS = 1
    FRAME_GAP = 1
    TITLE_LIMIT = 8
    ELLIPSIS = "…"
    TITLE_PREFIX = TITLE_LIMIT - len(ELLIPSIS)
    LINE_BREAK = "\n"
    SPACE = " "
    EMPTY_LINE = ""
    BODY_WRAP = False
    UNKNOWN_LEVEL = "Unknown alert level"
    WARNING_STATES = (
        RunStatus.INTERRUPTED,
        RunStatus.CANCELLED,
        RunStatus.PAUSED,
        *SessionSummary.WAITING_STATES,
    )

    def __init__(self, view):
        self.view = view
        self.app = self.NO_APP
        self.items = deque(maxlen=self.MAX_ITEMS)
        self._serial = self.INITIAL_SERIAL
        self._expiry_timer = self.NO_TIMER
        self._layout_key = self.UNCOMPUTED
        self._layout_value = ToastLayout(
            Window(height=self.EMPTY_HEIGHT), self.EMPTY_HEIGHT
        )
        self.container = DynamicContainer(self._container)

    def active(self):
        now = monotonic()
        kinds = self.view.general.notification_kinds
        if any(item.expires <= now or item.level not in kinds for item in self.items):
            self.items = deque(
                (
                    item
                    for item in self.items
                    if item.expires > now and item.level in kinds
                ),
                maxlen=self.MAX_ITEMS,
            )
        return bool(self.items)

    def width(self):
        return min(
            self.MAX_WIDTH,
            max(self.MIN_WIDTH, self.view._columns() - self.MARGIN_COLUMNS),
        )

    def height(self):
        return self._layout().height

    def _container(self):
        return self._layout().container

    def _layout(self):
        self.active()
        width, available = self.width(), max(
            self.EMPTY_HEIGHT, self.view._rows() - self.RESERVED_ROWS
        )
        key = ToastLayoutKey(
            width, available, tuple(self.items), self.view.t, self.view.t.icons
        )
        if key == self._layout_key:
            return self._layout_value
        frames, height = [], 0
        # Keep the newest alert readable when the terminal cannot fit the stack.
        # Borders + a two-cell icon + one cell on each side of the text = six.
        if width >= self.MIN_CONTENT_WIDTH and available >= self.MIN_CONTENT_ROWS:
            for item in reversed(self.items):
                title, icon = item.title, self.view.t.icons.alerts[item.level]
                text = self.view.t.status(item.status) if item.status else item.text
                lines = [
                    part
                    for line in text.split(self.LINE_BREAK)
                    for part in (
                        chop_cells(line, width - self.NON_TEXT_COLUMNS)
                        or [self.EMPTY_LINE]
                    )
                ]
                needed = len(lines) + self.BORDER_ROWS
                if frames and height + self.FRAME_GAP + needed > available:
                    break
                body_height = min(needed, available) - self.BORDER_ROWS
                title = (
                    title
                    if len(title) <= self.TITLE_LIMIT
                    else title[: self.TITLE_PREFIX] + self.ELLIPSIS
                )
                body = VSplit(
                    [
                        Window(
                            FormattedTextControl(icon),
                            width=self.ICON_COLUMNS,
                            align=WindowAlign.CENTER,
                        ),
                        Window(width=self.PADDING_COLUMNS),
                        Window(
                            FormattedTextControl(
                                self.LINE_BREAK.join(lines[:body_height])
                            ),
                            height=body_height,
                            wrap_lines=self.BODY_WRAP,
                        ),
                        Window(width=self.PADDING_COLUMNS),
                    ]
                )
                frames.append(
                    Frame(
                        body,
                        title=clipped_summary(title, width - self.NON_TEXT_COLUMNS),
                        style=ThemeStyle.alert_style(item.level),
                        height=body_height + self.BORDER_ROWS,
                    )
                )
                height += body_height + self.BORDER_ROWS + bool(height)
        self._layout_key = key
        self._layout_value = (
            ToastLayout(
                HSplit(list(reversed(frames)), padding=self.FRAME_GAP, height=height),
                height,
            )
            if frames
            else ToastLayout(Window(height=self.EMPTY_HEIGHT), self.EMPTY_HEIGHT)
        )
        return self._layout_value

    def _schedule_expiry(self, app):
        if self._expiry_timer is not self.NO_TIMER:
            self._expiry_timer.cancel()
            self._expiry_timer = self.NO_TIMER
        if self.items:

            def expired():
                self.active()
                self._schedule_expiry(app)
                app.invalidate()

            delay = max(0, min(item.expires for item in self.items) - monotonic())
            self._expiry_timer = asyncio.get_running_loop().call_later(delay, expired)

    def close(self):
        if self._expiry_timer is not self.NO_TIMER:
            self._expiry_timer.cancel()
            self._expiry_timer = self.NO_TIMER
        self.items.clear()
        self._layout_key = self.UNCOMPUTED
        self._layout_value = ToastLayout(
            Window(height=self.EMPTY_HEIGHT), self.EMPTY_HEIGHT
        )
        self.app = self.NO_APP

    def push(self, title, status, session_key):
        level = (
            NotificationKind.ERROR.value
            if status == RunStatus.FAILED
            else (
                NotificationKind.SUCCESS.value
                if status == RunStatus.COMPLETED
                else (
                    NotificationKind.WARNING.value
                    if status in self.WARNING_STATES
                    else NotificationKind.INFO.value
                )
            )
        )
        self.alert(title, self.view.t.status(status), level, session_key, status=status)

    def reconcile_waiting(self, project_id, sessions):
        waiting = {
            item.id for item in sessions if item.status in SessionSummary.WAITING_STATES
        }
        self.items = deque(
            (
                item
                for item in self.items
                if not item.removed_waiting_session(project_id, waiting)
            ),
            maxlen=self.MAX_ITEMS,
        )

    def alert(
        self,
        title,
        text,
        level=NotificationKind.INFO.value,
        key=AUTO_KEY,
        *,
        status=Alert.NO_STATUS
    ):
        if level not in self.view.t.icons.alerts:
            raise ValueError(self.UNKNOWN_LEVEL)
        if level not in self.view.general.notification_kinds:
            return
        app = self.app or get_app()
        self._serial += 1
        key = key if key is not self.AUTO_KEY else self._serial

        def clean(value):
            return self.SPACE.join(
                self.EMPTY_LINE.join(
                    char if char.isprintable() else self.SPACE for char in str(value)
                ).split()
            )

        self.items = deque(
            (item for item in self.items if item.key != key), maxlen=self.MAX_ITEMS
        )
        seconds = self.view.general.notification_seconds
        body = self.LINE_BREAK.join(clean(line) for line in str(text).splitlines())
        self.items.append(
            Alert(
                self._serial,
                key,
                clean(title),
                body,
                level,
                monotonic() + seconds,
                status,
            )
        )
        self._schedule_expiry(app)
        app.invalidate()
