"""Demand-driven reads for explicitly linked external actions, independent of popups."""
import asyncio
from datetime import datetime, time as wall_time, timedelta, timezone
import json
import time
from zoneinfo import ZoneInfo

from ...core.tasks import TaskSlot
from .actions import ActionLink
from .contracts import CalendarProvider
from .models import CalendarEvent


class ExternalSchedule(CalendarEvent):
    __slots__ = ()
    @property
    def delivery_once(self):
        return True


class ExternalActions:
    def __init__(self, runtime, connections, edits, changed):
        self.runtime, self.connections, self.edits = runtime, connections, edits
        self._slot = TaskSlot(changed)
        self._key = None
        self._wake = asyncio.Event()
        self._events, self._next = {}, {}
        self.error = ""

    @staticmethod
    def _windows(settings, now, horizon):
        offsets = {alarm.delta for alarm in settings.alarms}
        if settings.command.strip():
            offsets.add(timedelta())
        ranges = []
        for offset in offsets:
            # Delivery windows, translated to event starts; not the visible month.
            lower, upper = now + offset, now + offset + horizon
            ranges.append((lower, upper))
        merged = []
        for lower, upper in sorted(ranges):
            if merged and lower <= merged[-1][1]:
                merged[-1] = merged[-1][0], max(upper, merged[-1][1])
            else:
                merged.append((lower, upper))
        return tuple(merged)

    @staticmethod
    def _project(source, event, settings):
        def utc(value):
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            local = datetime.combine(value, wall_time())
            local = local.replace(tzinfo=ZoneInfo(event.timezone)) if event.timezone else local.astimezone()
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        # ID remains stable when an exception is moved. Receipts do not depend on
        # ETag, action revision or title, and imported text never supplies commands.
        identifier = "external:" + json.dumps((*source.identity, event.id), ensure_ascii=False, separators=(",", ":"))
        return ExternalSchedule(title=event.title or source.label, start=utc(event.start), end=utc(event.end),
            id=identifier, timezone="UTC", alarms=settings.alarms, command=settings.command, notes=event.notes)

    async def _fetch(self, source, catalog, now):
        targets = catalog.targets(source)
        provider = self.connections.require(source.connection, CalendarProvider)
        events = {}
        async with self.edits.reading(source):
            for binding in targets:
                if not binding.settings.active:
                    continue
                event = await provider.get(source, binding.link.event)
                if event is None:
                    continue
                if event.is_series:
                    for lower, upper in self._windows(binding.settings, now, timedelta(seconds=source.refresh_s * 2)):
                        for occurrence in await provider.instances(source, event.id, lower, upper):
                            events[occurrence.id] = occurrence
                else:
                    events[event.id] = event
        return tuple(self._project(source, event, settings) for event in events.values()
                     if (settings := catalog.effective(ActionLink.for_event(source, event))).active)

    async def _run(self, key, sources, catalog):
        while key == self._key and not self._slot.closed:
            self._wake.clear()
            for source in sources:
                if self._next[source.identity] > time.monotonic():
                    continue
                try:
                    events = await self._fetch(source, catalog, datetime.now(timezone.utc))
                    self.error = ""
                except Exception as exc:
                    events = ()  # Failed reads are not an authoritative empty calendar.
                    self.error = type(exc).__name__
                if key != self._key:
                    return
                self._events[source.identity] = events
                self._next[source.identity] = time.monotonic() + source.refresh_s
                self.runtime.set_external((item for group in self._events.values() for item in group), catalog)
            delay = max(0, min(self._next.values(), default=time.monotonic()) - time.monotonic())
            try:
                async with asyncio.timeout(delay):
                    await self._wake.wait()
            except TimeoutError:
                pass

    def refresh(self, source=None):
        if source is not None:
            self._events.pop(source.identity, None)
            self.runtime.set_external((item for group in self._events.values() for item in group), self.runtime.actions)
        self._next = dict.fromkeys(self._next, 0)
        self._wake.set()

    def sync(self, sources, *, start_task=asyncio.create_task):
        catalog = self.runtime.actions
        sources = tuple(source for source in sources if source.enabled
                        and any(item.settings.active for item in catalog.targets(source)))
        key = sources, catalog, self.connections.revision, CalendarEvent.recurrence_context()
        if key != self._key:
            self._key = key
            self._events, self._next = {}, dict.fromkeys((source.identity for source in sources), 0)
            self.runtime.set_external(())
        self._slot.sync(active=bool(sources), key=key, run=lambda: self._run(key, sources, catalog), start=start_task)

    def close(self):
        self._key = None
        self._slot.close()
        self._wake.set()
        self.runtime.set_external(())
