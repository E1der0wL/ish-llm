"""Bounded, invalidation-aware cache of public service observations only."""

from collections import OrderedDict
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Any
from zlib import crc32


class ReadKind(StrEnum):
    PROJECT = "project"
    AGENTS = "agents"
    SESSION = "session"
    RUN = "run"
    LATEST = "latest_run"
    PAUSED = "paused_runs"
    STEPS = "steps"
    CONVERSATION = "conversation"


@dataclass(slots=True)
class Observation:
    value: Any = None
    read_at: float | None = None
    pending: asyncio.Future | None = None


class ObservationReads:
    """Never used for admission, authorization or execution decisions.

    Invalidating an in-flight read also prevents its late result from entering
    the cache. Periodic expiry reconciles changes outside Hub's event stream.
    """

    LIMIT = 1024
    RECONCILE_SECONDS = 2.0
    BACKGROUND_SECONDS = 10.0
    BACKGROUND_SPREAD = 2.5
    PHASE_BUCKETS = 256

    def __init__(self):
        self.entries: OrderedDict[tuple, Observation] = OrderedDict()

    def clear(self) -> None:
        self.entries.clear()

    @classmethod
    def background_age(cls, identifier: str) -> float:
        """Stable per-session phases avoid one synchronized expiry burst.

        The upper bound remains ten seconds; event invalidation still bypasses
        expiry. This changes observation cadence only, never execution state.
        """
        phase = crc32(identifier.encode()) % cls.PHASE_BUCKETS
        return cls.BACKGROUND_SECONDS - cls.BACKGROUND_SPREAD * phase / cls.PHASE_BUCKETS

    def discard(self, *prefix) -> None:
        for key in tuple(self.entries):
            if key[:len(prefix)] == prefix:
                del self.entries[key]

    def remember(self, key: tuple, value: Any) -> None:
        """Receive an already persisted public event, never execution authority."""
        self.entries[key] = Observation(value, monotonic())
        self.entries.move_to_end(key)
        while len(self.entries) > self.LIMIT:
            self.entries.popitem(last=False)

    def fresh(self, key: tuple, max_age: float):
        cached = self.entries.get(key)
        if (cached is not None and cached.read_at is not None
                and monotonic() - cached.read_at < max_age):
            return cached.value
        return None

    async def read(self, key: tuple, load: Callable[[], Awaitable[Any]], *,
                   max_age: float = RECONCILE_SECONDS):
        cached = self.entries.get(key)
        if (cached is not None and cached.read_at is not None
                and monotonic() - cached.read_at < max_age):
            self.entries.move_to_end(key)
            return cached.value
        if cached is not None and cached.pending is not None:
            # Share only the same invalidation generation. Cancelling a waiting
            # screen must not cancel the owner or another screen's observation.
            try:
                return await asyncio.shield(cached.pending)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                # The owner was preempted by a command. Its storage drain has
                # finished; a remaining observer can make a fresh public read.
                return await self.read(key, load, max_age=max_age)
        pending = Observation(pending=asyncio.get_running_loop().create_future())
        shared = pending.pending
        self.entries[key] = pending
        self.entries.move_to_end(key)
        while len(self.entries) > self.LIMIT:
            self.entries.popitem(last=False)
        try:
            value = await load()
        except BaseException as error:
            if self.entries.get(key) is pending:
                del self.entries[key]
            if isinstance(error, asyncio.CancelledError):
                shared.cancel()
            else:
                shared.set_exception(error)
                shared.exception()  # The owner can be the only reader.
            raise
        if self.entries.get(key) is pending:
            pending.value, pending.read_at = value, monotonic()
        else:
            # A newer committed event can arrive while a service read waits on
            # storage. Do not publish that read over the newer observation.
            newer = self.entries.get(key)
            if newer is not None and newer.read_at is not None:
                value = newer.value
        pending.pending = None
        shared.set_result(value)
        return value
