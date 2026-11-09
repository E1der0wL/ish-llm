"""Calendar I/O and reminders live outside rendering and survive prompt gaps."""
import asyncio
from datetime import timezone
import time
from zoneinfo import ZoneInfo

from ...core.tasks import TaskSlot, run_blocking
from ...popups.models import PopupContent
from .store import CalendarSnapshot, CalendarStore
from .models import CalendarEvent
from .commands import CommandResult
from .actions import ActionCatalog
from ...locales import Locale, Message as M


class CalendarRuntime:
    def __init__(self, directory, invalidate, notify, *, submit=None, locale=Locale):
        self.store = CalendarStore(directory)
        self.snapshot = CalendarSnapshot()
        self.actions = ActionCatalog()
        self._external = ()
        self._external_actions = ActionCatalog()
        self.error = ""
        self._invalidate, self._notify = invalidate, notify
        self._submit, self._locale = submit, locale
        self._slot = TaskSlot(invalidate)
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._visible = False
        self._notifications_enabled = None
        self._key = None
        self._command_watch_armed = False

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
        self._wake.set()

    async def load_actions(self):
        self.actions = await self._io(self.store.load_actions)
        return self.actions

    async def save_actions(self, binding):
        self.actions, saved = await self._io(self.store.save_actions, binding)
        self.set_external(())
        self._wake.set()
        self._invalidate()
        return saved

    async def delete_actions(self, link):
        self.actions = await self._io(self.store.delete_actions, link)
        self.set_external(())
        self._wake.set()
        self._invalidate()

    def set_external(self, events, catalog=ActionCatalog()):
        """Replace verified expanded occurrences; provider errors withdraw their actions."""
        events = tuple(events)
        if events and catalog != self.actions:
            return  # A read finishing after local action changes cannot re-arm old commands.
        if events != self._external or catalog != self._external_actions:
            self._external = events
            self._external_actions = catalog
            self._wake.set()

    async def save(self, event):
        snapshot, saved = await self._io(self.store.save, event)
        self._publish(snapshot)
        self._wake.set()
        return saved

    async def delete(self, event):
        self._publish(await self._io(self.store.delete, event))
        self._wake.set()

    @staticmethod
    def display_start(event, clock):
        # Runtime clocks historically require only now(); a named display zone
        # is optional, with the same system-local default as ClockConfig.
        zone_name = getattr(clock, "timezone", None)
        zone = ZoneInfo(zone_name) if zone_name else None
        return event.localize(event.start).astimezone(zone)

    async def _run(self, config, clock, notifications):
        next_at, watch_at, context = None, 0.0, None
        key = config.runtime_key
        if not self._command_watch_armed:
            await self._io(self.store.reset_command_watch)
            self._command_watch_armed = True
        # A timeout also cancels its task briefly. Close/reconfigure can coincide
        # with that signal, so the owner's state must terminate the loop too.
        while config.enabled and self._key == key and not self._slot.closed:
            requested = self._wake.is_set()
            self._wake.clear()
            now = clock().now().astimezone(timezone.utc)
            current_context = CalendarEvent.recurrence_context()
            try:
                if requested or time.monotonic() >= watch_at or current_context != context or (next_at is not None and now >= next_at):
                    check = await self._io(self.store.check, now, config.reminders and notifications(), self._submit is not None,
                                           self._external, self._external_actions)
                    self._publish(check.snapshot)
                    if self.actions != check.actions:
                        self.actions = check.actions
                        self._invalidate()
                    next_at, context = check.next_at, current_context
                    watch_at = time.monotonic() + config.watch_interval(self._visible)
                    if self._slot.closed or self._key != key:
                        continue
                    for reminder in check.due:
                        event = reminder.event
                        text = self.display_start(event, clock()).strftime("%Y-%m-%d %H:%M")
                        self._notify(event.title, text, details=PopupContent.plain(event.title, text + "\n\n" + event.notes))
                    for occurrence in check.commands:
                        event = occurrence.event
                        try:
                            outcome = self._submit(event.command)
                        except Exception:
                            outcome = CommandResult.FAILED
                        if outcome != CommandResult.SUBMITTED:
                            message = self._locale().text(M.COMMAND_SKIPPED if outcome == CommandResult.SKIPPED else M.COMMAND_FAILED)
                            details = self.display_start(event, clock()).isoformat(sep=" ") + "\n" + event.command + "\n\n" + message
                            self._notify(event.title, message, details=PopupContent.plain(event.title, details))
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                if self.error != message:
                    self.error = message
                    self._invalidate()
                next_at = None
                watch_at = time.monotonic() + config.watch_interval(self._visible)
            delay = max(0, watch_at - time.monotonic())
            if next_at is not None:
                delay = min(delay, config.clock_s, max(0, next_at.timestamp() - clock().now().timestamp()))
            try:
                async with asyncio.timeout(delay):
                    await self._wake.wait()
            except TimeoutError:
                pass

    def sync(self, config, clock, notifications, *, visible=False):
        if not config.enabled:
            self._command_watch_armed = False
        enabled = notifications()
        key = config.runtime_key
        if key != self._key or visible != self._visible or enabled != self._notifications_enabled:
            self._key = key
            self._visible, self._notifications_enabled = visible, enabled
            self._wake.set()
        self._slot.sync(active=config.enabled, key=key,
                        run=lambda: self._run(config, clock, notifications), start=asyncio.create_task)

    def close(self):
        self._slot.close()
        self._wake.set()
