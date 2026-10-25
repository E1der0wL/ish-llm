"""Thread-safe latest-snapshot mailbox; scheduling remains with the UI controller."""

from threading import Lock

from ..model import HubSnapshot


class SnapshotInbox:
    EMPTY = None
    UNCLAIMED = False
    CLAIMED = True

    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: HubSnapshot | None = SnapshotInbox.EMPTY
        self._scheduled = SnapshotInbox.UNCLAIMED

    def offer(self, snapshot: HubSnapshot, *, can_schedule: bool) -> bool:
        """Replace pending data and claim at most one UI-loop delivery slot."""
        with self._lock:
            self._pending = snapshot
            if self._scheduled or not can_schedule:
                return SnapshotInbox.UNCLAIMED
            self._scheduled = SnapshotInbox.CLAIMED
            return SnapshotInbox.CLAIMED

    def take(self) -> HubSnapshot | None:
        with self._lock:
            snapshot = self._pending
            self._pending = SnapshotInbox.EMPTY
            self._scheduled = SnapshotInbox.UNCLAIMED
            return snapshot

    def rebind(self) -> None:
        """A replacement shell prompt loop cannot consume the old scheduled callback."""
        with self._lock:
            self._scheduled = SnapshotInbox.UNCLAIMED
