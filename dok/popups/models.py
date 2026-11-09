"""Content contracts and FIFO notification transitions, independent of the host."""
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..core.records import Boolean, Text
from ..core.theme import Theme
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
    footer: bool = True
    compact: bool = False

    def __post_init__(self):
        Text(single_line=True).decode(self.title)
        Boolean().decode(self.footer)
        Boolean().decode(self.compact)
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

    def rows_at(self, columns: int, theme: Theme) -> tuple[TextRow, ...]:
        """Produce body rows for the width owned by the popup renderer."""
        return self.rows


class PopupPage(Protocol):
    def content(self) -> PopupContent: ...


@dataclass(frozen=True, slots=True)
class Notification:
    content: PopupContent
    duration: float | None
    details: PopupPage | None = None
    key: str | None = None


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
        if notification.key is not None:
            if self._current is not None and self._current.key == notification.key:
                self._current = notification
                self._remaining, self._since = notification.duration, None
                return
            for index, queued in enumerate(self._queue):
                if queued.key == notification.key:
                    self._queue[index] = notification
                    return
        self._queue.append(notification)

    def remove(self, key: str) -> None:
        """Withdraw only one producer's current or queued notification."""
        if self._current is not None and self._current.key == key:
            self.dismiss()
        self._queue = deque(item for item in self._queue if item.key != key)

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
