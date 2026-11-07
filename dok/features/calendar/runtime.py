"""Calendar I/O and reminders live outside rendering and survive prompt gaps."""
import asyncio

from ...core.tasks import TaskSlot, run_blocking
from ...popups.models import PopupContent
from .store import CalendarSnapshot, CalendarStore


class CalendarRuntime:
    def __init__(self, directory, invalidate, notify):
        self.store = CalendarStore(directory)
        self.snapshot = CalendarSnapshot()
        self.error = ""
        self._invalidate, self._notify = invalidate, notify
        self._slot = TaskSlot(invalidate)
        self._lock = asyncio.Lock()

    def _publish(self, snapshot):
        changed = snapshot != self.snapshot
        if changed:
            self.snapshot = snapshot
        if changed or self.error:
            self.error = ""
            self._invalidate()

    async def _io(self, function, *args):
        # Cancellation waits for disk I/O before a successor can enter the store.
        async with self._lock:
            return await run_blocking(function, *args)

    async def refresh(self):
        self._publish(await self._io(self.store.load))

    async def save(self, event):
        snapshot, saved = await self._io(self.store.save, event)
        self._publish(snapshot)
        return saved

    async def delete(self, event):
        self._publish(await self._io(self.store.delete, event))

    async def _run(self, config, clock, notifications):
        while True:
            try:
                if config.reminders and notifications():
                    snapshot, due = await self._io(self.store.claim_due, clock().now())
                    self._publish(snapshot)
                    for event in due:
                        text = event.start.strftime("%Y-%m-%d %H:%M") + (" · " + event.location if event.location else "")
                        self._notify(event.title, text, details=PopupContent.plain(event.title, text + "\n\n" + event.notes))
                else:
                    self._publish(await self._io(self.store.load))
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                if self.error != message:
                    self.error = message
                    self._invalidate()
            await asyncio.sleep(config.poll_s)

    def sync(self, config, clock, notifications):
        self._slot.sync(active=config.enabled, key=(config.enabled, config.reminders, config.poll_s),
                        run=lambda: self._run(config, clock, notifications), start=asyncio.create_task)

    def close(self):
        self._slot.close()
