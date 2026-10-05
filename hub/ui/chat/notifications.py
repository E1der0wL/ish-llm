"""Typed, non-focusable alerts shared by Hub and the host shell."""

import asyncio
from collections import deque
from dataclasses import dataclass
from time import monotonic
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame
from ...asset import icon


@dataclass(frozen=True, slots=True)
class Alert:
    identity: int
    key: object
    title: str
    text: str
    level: str
    expires: float


class SessionToasts:
    def __init__(self, view):
        self.view = view
        self.app = None
        self.items = deque(maxlen=3)
        self._serial = 0
        self.container = DynamicContainer(self._container)

    def active(self):
        self.items = deque((item for item in self.items if item.expires > monotonic()
                            and item.level in self.view.general.notification_kinds), maxlen=3)
        return bool(self.items)

    def width(self):
        return min(44, max(12, self.view._columns() - 4))

    def height(self):
        self.active()
        return min(max(1, len(self.items) * 4 - 1), max(1, self.view._rows() - 3))

    def text(self):
        self.active()
        return "\n\n".join(f"{item.title}\n{icon.ALERT[item.level]} {item.text}" for item in self.items)

    def _container(self):
        self.active()
        return HSplit([Frame(Window(FormattedTextControl(f"{icon.ALERT[item.level]} {item.text}"),
                                    height=1, wrap_lines=False), title=item.title,
                             style=f"class:hub.alert-{item.level}", height=3)
                       for item in self.items], padding=1) if self.items else Window(height=1)

    def push(self, title, status, session_key):
        level = ("error" if status == "failed" else "success" if status == "completed" else
                 "warning" if status in ("interrupted", "cancelled", "paused") else "info")
        self.alert(title, self.view.t.status(status), level, session_key)

    def alert(self, title, text, level="info", key=None):
        if level not in icon.ALERT:
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
        self.items.append(Alert(self._serial, key, clean(title)[:80], clean(text), level, monotonic() + seconds))
        asyncio.get_running_loop().call_later(seconds, app.invalidate)
        app.invalidate()
