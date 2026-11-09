"""Compose local schedules with cached external reads; remote data never enters the scheduler."""
import asyncio
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import time

from ...connections.models import ConnectionError, ConnectionState, ConnectionStatus
from ...core.tasks import TaskSlot
from .contracts import CalendarEntry, CalendarProvider, CalendarSource, CalendarWindow, ExternalEvent
from .models import CalendarEvent
from .store import CalendarSnapshot
from .edits import CalendarEdits
from .writes import CalendarWriter
from .action_watch import ExternalActions


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source: CalendarSource
    status: ConnectionStatus
    events: tuple[ExternalEvent, ...] = ()
    updated_at: datetime | None = None
    stale: bool = False
    writable: bool = False


@dataclass(frozen=True, slots=True)
class CalendarListing:
    local: CalendarSnapshot
    external: tuple[CalendarEntry, ...] = ()

    def on_day(self, day: date):
        return tuple(sorted((*self.local.on_day(day), *(item for item in self.external if item.on_day(day))),
                            key=lambda item: (item.start.replace(tzinfo=None), item.title)))

    def get(self, identifier: str | tuple[str, str, str]):
        return (next((item for item in self.external if item.id == identifier), None)
                if isinstance(identifier, tuple) else self.local.get(identifier))


class CalendarService:
    def __init__(self, local, connections, invalidate):
        self.local, self.connections = local, connections
        self._invalidate = invalidate
        self._slot = TaskSlot(invalidate)
        self._wake = asyncio.Event()
        self._key = None
        self._sources = ()
        self._states, self._next = {}, {}
        self._window = None
        self._active = self._working = False
        self._listing_key = self._listing = None
        self.edits = CalendarEdits(connections, lambda: self.sources, self._published_edit, actions=local)
        self.actions = ExternalActions(local, connections, self.edits, invalidate)

    def _published_edit(self, source, event_id, event, revision):
        if revision != self.connections.revision or source not in self._sources:
            return
        state = self._states[source.identity]
        if event is not None and event in state.events:
            return
        if event is not None and event.is_series:
            # A master record cannot replace its expanded occurrences. Keep the
            # last view until a complete fresh month has been retrieved.
            self._states[source.identity] = replace(state, stale=True)
        else:
            events = tuple(item for item in state.events if item.id != event_id and item.series_id != event_id)
            self._states[source.identity] = replace(state, events=events + ((event,) if event is not None else ()))
        self._next[source.identity] = 0
        self._wake.set()
        self.actions.refresh(source)
        self._listing_key = None
        self._invalidate()

    async def _fetch(self, source, key):
        previous = self._states[source.identity]
        try:
            async with self.edits.reading(source):
                previous = self._states.get(source.identity, previous)
                provider = self.connections.require(source.connection, CalendarProvider)
                events = await provider.fetch(source, self._window)
                writer = self.connections.resolve(source.connection, CalendarWriter)
                current = SourceSnapshot(source, ConnectionStatus(ConnectionState.READY), events, datetime.now(timezone.utc),
                                         writable=writer is not None and writer.can_write(source))
            next_at = time.monotonic() + source.refresh_s
        except ConnectionError as exc:
            # Failed reads are distinct from an empty calendar. Authorization
            # failures discard cached private content; other failures retain it.
            keep = exc.status.state == ConnectionState.FAILED
            current = SourceSnapshot(source, exc.status, previous.events if keep else (),
                previous.updated_at if keep else None, keep and previous.updated_at is not None)
            next_at = float("inf")  # Explicit reopen/F5 retries a failed read.
        except Exception:
            current = SourceSnapshot(source, ConnectionStatus(ConnectionState.FAILED, "Calendar source failed"),
                                     previous.events, previous.updated_at, previous.updated_at is not None)
            next_at = float("inf")
        if key == self._key:
            self._states[source.identity], self._next[source.identity] = current, next_at
            self._listing_key = None
            self._invalidate()

    async def _run(self, key):
        while key == self._key:
            self._wake.clear()
            due = [source for source in self._sources if self._next[source.identity] <= time.monotonic()]
            self._working = bool(due)
            if due:
                self._invalidate()
            try:
                await asyncio.gather(*(self._fetch(source, key) for source in due))
            finally:
                self._working = False
            if key != self._key:
                return
            if self._wake.is_set():
                continue
            deadline = min(self._next.values(), default=float("inf"))
            if deadline == float("inf"):
                await self._wake.wait()
            else:
                try:
                    async with asyncio.timeout(max(0, deadline - time.monotonic())):
                        await self._wake.wait()
                except TimeoutError:
                    pass

    @property
    def sources(self) -> tuple[SourceSnapshot, ...]:
        return tuple(self._states[source.identity] for source in self._sources)

    @property
    def snapshot(self) -> CalendarListing:
        local = self.local.snapshot
        key = (local, self.sources, self._window, CalendarEvent.recurrence_context())
        if self._listing_key != key:
            zone = self._window.timezone if self._window else None
            events = tuple(event.project(state.source, zone) for state in self.sources for event in state.events)
            self._listing, self._listing_key = CalendarListing(local, events), key
        return self._listing

    @property
    def working(self) -> bool:
        return self._working

    def sync(self, sources: tuple[CalendarSource, ...], window: CalendarWindow | None, *, start_task=asyncio.create_task) -> None:
        sources = tuple(source for source in sources if source.enabled)
        self.actions.sync(sources, start_task=start_task)
        active = window is not None and bool(sources)
        # Hidden popups retain only their last month's in-memory cache; no
        # background polling or persisted remote event copies are introduced.
        target_window = window or self._window
        key = (sources, target_window, self.connections.revision)
        if key != self._key:
            self._key, self._sources, self._window = key, sources, target_window
            self._states = {s.identity: SourceSnapshot(s, ConnectionStatus(ConnectionState.SETUP)) for s in sources}
            self._next = {s.identity: 0 for s in sources}
            self._listing_key = None
        if active and not self._active:
            for source in sources:
                if self._states[source.identity].status.state != ConnectionState.READY:
                    self._next[source.identity] = 0
        self._active = active
        async def run():
            try:
                await self._run(key)
            finally:
                if key == self._key:
                    self._active = False
        self._slot.sync(active=active, key=key, run=run, start=start_task)

    def refresh(self) -> None:
        self.actions.refresh()
        if not self._working:
            self._next = {source.identity: 0 for source in self._sources}
            self._wake.set()
            self._invalidate()

    def close(self) -> None:
        self.actions.close()
        self._key = None
        self._active = False
        self._slot.close()
        self._wake.set()
