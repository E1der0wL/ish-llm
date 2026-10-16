"""Typed, non-focusable alerts shared by Hub and the host shell."""

import asyncio
from collections import deque
from dataclasses import dataclass
from time import monotonic
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame
from rich.cells import chop_cells

from ..summary import clipped_summary


@dataclass(frozen=True, slots=True)
class Alert:
    identity: int
    key: object
    title: str
    text: str
    level: str
    expires: float
    status: str = ""


class SessionToasts:
    def __init__(self, view):
        self.view = view
        self.app = None
        self.items = deque(maxlen=3)
        self._serial = 0
        self._expiry_timer = None
        self._layout_key = None
        self._layout_value = (Window(height=1), 1)
        self.container = DynamicContainer(self._container)

    def active(self):
        now = monotonic()
        kinds = self.view.general.notification_kinds
        if any(item.expires <= now or item.level not in kinds for item in self.items):
            self.items = deque((item for item in self.items if item.expires > now
                                and item.level in kinds), maxlen=3)
        return bool(self.items)

    def width(self):
        return min(48, max(1, self.view._columns() - 4))

    def height(self):
        return self._layout()[1]

    def _container(self):
        return self._layout()[0]

    def _layout(self):
        self.active()
        width, available = self.width(), max(1, self.view._rows() - 3)
        key = (width, available, tuple(self.items), self.view.t, self.view.t.icons)
        if key == self._layout_key:
            return self._layout_value
        frames, height = [], 0
        # Keep the newest alert readable when the terminal cannot fit the stack.
        # Borders + a two-cell icon + one cell on each side of the text = six.
        if width >= 8 and available >= 3:
            for item in reversed(self.items):
                title, icon = item.title, self.view.t.icons.alerts[item.level]
                text = self.view.t.status(item.status) if item.status else item.text
                lines = [part for line in text.split("\n")
                         for part in (chop_cells(line, width - 6) or [""])]
                needed = len(lines) + 2
                if frames and height + 1 + needed > available:
                    break
                body_height = min(needed, available) - 2
                title = title if len(title) <= 8 else title[:7] + "…"
                body = VSplit([
                    Window(FormattedTextControl(icon), width=2, align=WindowAlign.CENTER),
                    Window(width=1),
                    Window(FormattedTextControl("\n".join(lines[:body_height])),
                           height=body_height, wrap_lines=False),
                    Window(width=1),
                ])
                frames.append(Frame(body, title=clipped_summary(title, width - 6),
                                    style=f"class:hub.alert-{item.level}", height=body_height + 2))
                height += body_height + 2 + bool(height)
        self._layout_key = key
        self._layout_value = ((HSplit(list(reversed(frames)), padding=1, height=height), height)
                              if frames else (Window(height=1), 1))
        return self._layout_value

    def _schedule_expiry(self, app):
        if self._expiry_timer is not None:
            self._expiry_timer.cancel()
            self._expiry_timer = None
        if self.items:
            def expired():
                self.active()
                self._schedule_expiry(app)
                app.invalidate()
            delay = max(0, min(item.expires for item in self.items) - monotonic())
            self._expiry_timer = asyncio.get_running_loop().call_later(delay, expired)

    def close(self):
        if self._expiry_timer is not None:
            self._expiry_timer.cancel()
            self._expiry_timer = None
        self.items.clear()
        self._layout_key = None
        self._layout_value = (Window(height=1), 1)
        self.app = None

    def push(self, title, status, session_key):
        level = ("error" if status == "failed" else "success" if status == "completed" else
                 "warning" if status in ("interrupted", "cancelled", "paused", "waiting_input", "waiting_approval") else "info")
        self.alert(title, self.view.t.status(status), level, session_key, status=status)

    def reconcile_waiting(self, project_id, sessions):
        waiting = {item.id for item in sessions if item.status in ("waiting_input", "waiting_approval")}
        self.items = deque((item for item in self.items if not (
            item.status in ("waiting_input", "waiting_approval") and isinstance(item.key, tuple)
            and len(item.key) == 2 and item.key[0] == project_id and item.key[1] not in waiting)), maxlen=3)

    def alert(self, title, text, level="info", key=None, *, status=""):
        if level not in self.view.t.icons.alerts:
            raise ValueError("Unknown alert level")
        if level not in self.view.general.notification_kinds:
            return
        app = self.app or get_app()
        self._serial += 1
        key = key if key is not None else self._serial
        def clean(value):
            return " ".join("".join(char if char.isprintable() else " " for char in str(value)).split())
        self.items = deque((item for item in self.items if item.key != key), maxlen=3)
        seconds = self.view.general.notification_seconds
        body = "\n".join(clean(line) for line in str(text).splitlines())
        self.items.append(Alert(self._serial, key, clean(title), body, level, monotonic() + seconds, status))
        self._schedule_expiry(app)
        app.invalidate()
