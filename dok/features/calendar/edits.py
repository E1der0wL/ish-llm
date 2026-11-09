"""Serialize remote reads/writes and retain uncertain requests without replaying them."""
import asyncio
from contextlib import asynccontextmanager

from ...connections.models import ConnectionError
from .contracts import CalendarSource, ExternalEvent
from .writes import CalendarWriter, CalendarMutation, CalendarConflict, CalendarWriteError, WriteProblem
from .actions import ActionCatalog, ActionLink


class ActionSavePending(Exception):
    """The provider write succeeded. Retry only the local action persistence."""
    def __init__(self, event, binding=None, deleted=None):
        self.event, self.binding, self.deleted = event, binding, deleted
        super().__init__("Calendar saved; dok execution settings are pending")


class CalendarEdits:
    def __init__(self, connections, sources, publish, *, actions=None):
        self._connections, self._sources, self._publish = connections, sources, publish
        self._locks, self._pending = {}, {}
        self._actions = actions

    async def action_catalog(self):
        return await self._actions.load_actions() if self._actions is not None else ActionCatalog()

    async def finish_actions(self, pending):
        if self._actions is not None:
            if pending.deleted is not None:
                await self._actions.delete_actions(pending.deleted)
            elif pending.binding is not None:
                await self._actions.save_actions(pending.binding)
        return pending.event

    async def save(self, mutation, binding=None):
        """Provider and local stores have separate commits; never replay a completed write."""
        if binding is not None:
            if binding.link.identity != (*mutation.source.identity, mutation.event_id):
                raise ValueError("Execution settings must belong to the schedule being saved")
            if self._actions is None:
                raise ValueError("Calendar execution storage is unavailable")
        event = await self.apply(mutation)
        deleted = (ActionLink.for_event(mutation.source, mutation.original)
                   if mutation.values is None else None)
        pending = ActionSavePending(event, binding, deleted)
        try:
            return await self.finish_actions(pending)
        except Exception as exc:
            raise pending from exc

    def _writer(self, source):
        if source not in self.destinations:
            raise CalendarWriteError(WriteProblem.DENIED)
        try:
            writer = self._connections.require(source.connection, CalendarWriter)
        except ConnectionError as exc:
            raise CalendarWriteError(WriteProblem.DENIED) from exc
        if not writer.can_write(source):
            raise CalendarWriteError(WriteProblem.DENIED)
        return writer

    @property
    def destinations(self) -> tuple[CalendarSource, ...]:
        result = []
        for state in self._sources():
            try:
                writer = self._connections.resolve(state.source.connection, CalendarWriter)
                if state.writable and writer is not None and writer.can_write(state.source):
                    result.append(state.source)
            except ConnectionError:
                continue
        return tuple(result)

    def source_for(self, identifier: tuple[str, str, str]) -> CalendarSource | None:
        return next((source for source in self.destinations if source.identity == identifier[:2]), None)

    def editable(self, identifier: tuple[str, str, str]) -> bool:
        source = self.source_for(identifier)
        state = next((state for state in self._sources() if source is not None and state.source == source), None)
        return state is not None and any(event.id == identifier[2] and event.editable for event in state.events)

    @asynccontextmanager
    async def reading(self, source):
        lock = self._locks.setdefault(source.identity, asyncio.Lock())
        async with lock:
            yield

    async def get(self, source: CalendarSource, event_id: str) -> ExternalEvent | None:
        async with self.reading(source):
            revision = self._connections.revision
            event = await self._writer(source).get(source, event_id)
            if event is not None:
                self._pending.pop((*source.identity, event_id), None)
            if event is None or not event.is_series:
                self._publish(source, event_id, event, revision)
            return event

    async def edit_target(self, source: CalendarSource, event_id: str) -> ExternalEvent:
        """Editing a recurrence always targets its owning series, including deletion."""
        event = await self.get(source, event_id)
        if event is None:
            raise CalendarWriteError(WriteProblem.UNAVAILABLE)
        if event.series_id:
            parent_id = event.series_id
            event = await self.get(source, parent_id)
            if event is None:
                raise CalendarWriteError(WriteProblem.UNAVAILABLE)
            if event.id != parent_id or event.series_id:
                raise CalendarWriteError(WriteProblem.FAILED)
        if not event.editable:
            raise CalendarWriteError(WriteProblem.DENIED)
        return event

    async def apply(self, mutation: CalendarMutation) -> ExternalEvent | None:
        identity = (*mutation.source.identity, mutation.event_id)
        async with self.reading(mutation.source):
            writer = self._writer(mutation.source)
            if identity in self._pending:
                raise CalendarWriteError(WriteProblem.UNCERTAIN)
            revision = self._connections.revision
            self._pending[identity] = mutation
            try:
                event = await writer.apply(mutation)
            except CalendarConflict as exc:
                self._pending.pop(identity, None)
                if exc.latest is not None:
                    self._publish(mutation.source, mutation.event_id, exc.latest, revision)
                raise
            except CalendarWriteError as exc:
                if exc.problem != WriteProblem.UNCERTAIN:
                    self._pending.pop(identity, None)
                raise
            except asyncio.CancelledError:
                # A provider must classify a possibly-sent request as uncertain.
                # Cancellation before dispatch is safe to release.
                self._pending.pop(identity, None)
                raise
            except Exception as exc:
                raise CalendarWriteError(WriteProblem.UNCERTAIN) from exc
            self._pending.pop(identity, None)
            self._publish(mutation.source, mutation.event_id, event, revision)
            return event
