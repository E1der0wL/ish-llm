"""Content contracts and FIFO notification transitions, independent of the host."""
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..core.records import Text
from ..rendering.presentation import TextRow


@dataclass(frozen=True, slots=True)
class PopupAction:
    label: str
    invoke: Callable[[], None]

    def __post_init__(self):
        Text(nonblank=True, single_line=True).decode(self.label)
        if not callable(self.invoke):
            raise TypeError("Popup action must be callable")


@dataclass(frozen=True, slots=True)
class PopupContent:
    title: str
    rows: tuple[TextRow, ...]
    actions: tuple[PopupAction, ...] = ()

    def __post_init__(self):
        Text(single_line=True).decode(self.title)
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "actions", tuple(self.actions))
        if not all(isinstance(row, TextRow) for row in self.rows):
            raise TypeError("Popup rows must be TextRow instances")
        if not all(isinstance(action, PopupAction) for action in self.actions):
            raise TypeError("Popup actions must be PopupAction instances")

    @classmethod
    def plain(cls, title: str, text: str, actions=()):
        Text().decode(text)
        return cls(title, tuple(TextRow.plain(line, "") for line in text.splitlines()), tuple(actions))

    def content(self):
        return self


class PopupPage(Protocol):
    def content(self) -> PopupContent: ...


@dataclass(frozen=True, slots=True)
class Notification:
    content: PopupContent
    duration: float | None
    details: PopupPage | None = None


class NotificationQueue:
    """Countdown consumes visible time only. No timer thread or persistence."""

    def __init__(self):
        self._queue: deque[Notification] = deque()
        self._current: Notification | None = None
        self._remaining: float | None = None
        self._since: float | None = None

    @property
    def current(self) -> Notification | None:
        return self._current

    @property
    def pending(self) -> int:
        return len(self._queue) + int(self._current is not None)

    @property
    def remaining(self) -> float | None:
        return self._remaining

    def add(self, notification: Notification) -> None:
        self._queue.append(notification)

    def dismiss(self) -> None:
        self._current = None
        self._remaining = self._since = None

    def clear(self) -> None:
        self.dismiss()
        self._queue.clear()

    def sync(self, visible: bool, now: float) -> None:
        if self._since is not None and self._remaining is not None:
            self._remaining = max(0, self._remaining - max(0, now - self._since))
            if self._remaining == 0:
                self.dismiss()
        self._since = None
        if visible:
            if self._current is None and self._queue:
                self._current = self._queue.popleft()
                self._remaining = self._current.duration
            if self._current is not None:
                self._since = now
