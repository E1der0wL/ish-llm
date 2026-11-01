"""One CPU render lane, with a single replaceable batch of fixed UI slots."""

import asyncio
from dataclasses import dataclass
from threading import Condition, Thread
from time import perf_counter
from typing import NamedTuple, Callable
from .work import RenderWork


@dataclass(slots=True)
class RenderStats:
    requested: int = 0
    rendered: int = 0
    discarded: int = 0
    failures: int = 0
    seconds: float = 0
    pending_peak: int = 0
    yielded: int = 0


class RenderSlot:
    TRANSCRIPT = "transcript"
    POPUP = "popup"
    SEARCH = "search"
    QUESTION = "question"
    ALL = (TRANSCRIPT, POPUP, SEARCH, QUESTION)


class RenderToken(NamedTuple):
    generation: int
    epoch: int
    progressive: bool


class PendingRender(NamedTuple):
    token: RenderToken
    loop: object
    build: Callable
    deliver: Callable
    fallback: Callable
    work: RenderWork | None = None


class RenderWorker:
    """Never submit CPU work to asyncio's unbounded default executor queue.

    One running job and one pending batch (at most four fixed surfaces). A
    surface replaces its pending job; unrelated surfaces cannot starve its final
    result. Only the UI loop delivers results. Progressive surfaces publish
    completed intermediate frames; cancellation epochs reject old sessions.
    Other surfaces retain strict latest-generation delivery.
    """

    slots = frozenset(RenderSlot.ALL)
    INITIAL_GENERATION = 0
    INITIAL_EPOCH = 0
    REVISION_STEP = 1
    ABSENT = None
    OPEN = False
    CLOSED = True
    PROGRESSIVE = False
    THREAD_NAME = "hub-render"
    DAEMON = True

    def __init__(self):
        self.stats = RenderStats()
        self._condition = Condition()
        self._pending = {}
        self._generation = dict.fromkeys(self.slots, self.INITIAL_GENERATION)
        self._epoch = dict.fromkeys(self.slots, self.INITIAL_EPOCH)
        self._thread = self.ABSENT
        self._closed = self.OPEN

    def cancel(self, slot):
        with self._condition:
            self._generation[slot] += self.REVISION_STEP
            self._epoch[slot] += self.REVISION_STEP
            removed = self._pending.pop(slot, self.ABSENT)
        if removed is not None and removed.work is not None:
            removed.work.close()

    def request(self, slot, build, deliver, fallback, *, progressive=PROGRESSIVE):
        if slot not in self.slots:
            raise ValueError(slot)
        loop = asyncio.get_running_loop()
        with self._condition:
            if self._closed:
                return
            self._generation[slot] += self.REVISION_STEP
            generation = self._generation[slot]
            self.stats.requested += 1
            removed = self._pending.get(slot)
            if removed is not None:
                self.stats.discarded += 1
            token = RenderToken(generation, self._epoch[slot], progressive)
            self._pending[slot] = PendingRender(token, loop, build, deliver, fallback)
            self.stats.pending_peak = max(self.stats.pending_peak, len(self._pending))
            if self._thread is self.ABSENT:
                self._thread = Thread(
                    target=self._run, name=self.THREAD_NAME, daemon=self.DAEMON
                )
                self._thread.start()
            self._condition.notify()
        if removed is not None and removed.work is not None:
            removed.work.close()

    def _current(self, slot, token):
        generation, epoch, progressive = token
        return (
            not self._closed
            and self._epoch[slot] == epoch
            and (progressive or self._generation[slot] == generation)
        )

    def _deliver(self, slot, token, deliver, result):
        with self._condition:
            current = self._current(slot, token)
        if current:
            deliver(result)

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                slot = next(iter(self._pending))
                job = self._pending.pop(slot)
                token, loop, build, deliver, fallback, work = job
            started = perf_counter()
            complete = True
            try:
                result = work if work is not None else build()
                if isinstance(result, RenderWork):
                    work = result
                    if token.progressive:
                        # Transcript frames retain their existing progressive
                        # delivery guarantee. Strict popups/search yield slices.
                        result = work.finish()
                    elif work.advance():
                        result = work.result
                    else:
                        complete = False
            except Exception as error:
                if work is not None:
                    work.close()
                self.stats.failures += 1
                try:
                    result = fallback(error)
                except Exception:
                    continue
            finally:
                self.stats.seconds += perf_counter() - started
                self.stats.rendered += complete
            with self._condition:
                current = self._current(slot, token)
                if not complete and current and slot not in self._pending:
                    self._pending[slot] = job._replace(work=work)
                    self.stats.yielded += 1
                    self.stats.pending_peak = max(self.stats.pending_peak, len(self._pending))
                    continue
            if not complete:
                work.close()
                self.stats.discarded += 1
                continue
            if current:
                try:
                    loop.call_soon_threadsafe(
                        self._deliver, slot, token, deliver, result
                    )
                except RuntimeError:  # Host prompt loop was closed while hidden.
                    pass
            else:
                self.stats.discarded += 1

    def close(self):
        with self._condition:
            self._closed = self.CLOSED
            removed = tuple(self._pending.values())
            self._pending.clear()
            self._condition.notify()
        for job in removed:
            if job.work is not None:
                job.work.close()
