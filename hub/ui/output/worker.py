"""One CPU render lane, with a single replaceable batch of fixed UI slots."""

import asyncio
from dataclasses import dataclass
from threading import Condition, Thread
from time import perf_counter


@dataclass(slots=True)
class RenderStats:
    requested: int = 0
    rendered: int = 0
    discarded: int = 0
    failures: int = 0
    seconds: float = 0
    pending_peak: int = 0


class RenderWorker:
    """Never submit CPU work to asyncio's unbounded default executor queue.

    One running job and one pending batch (at most four fixed surfaces). A
    surface replaces its pending job; unrelated surfaces cannot starve its final
    result. Only the UI loop delivers results, with a second generation check.
    """

    slots = frozenset({"transcript", "preview", "popup", "search"})

    def __init__(self):
        self.stats = RenderStats()
        self._condition = Condition()
        self._pending = {}
        self._generation = dict.fromkeys(self.slots, 0)
        self._thread = None
        self._closed = False

    def cancel(self, slot):
        with self._condition:
            self._generation[slot] += 1
            self._pending.pop(slot, None)

    def request(self, slot, build, deliver, fallback):
        if slot not in self.slots:
            raise ValueError(slot)
        loop = asyncio.get_running_loop()
        with self._condition:
            if self._closed:
                return
            self._generation[slot] += 1
            generation = self._generation[slot]
            self.stats.requested += 1
            if slot in self._pending:
                self.stats.discarded += 1
            self._pending[slot] = (generation, loop, build, deliver, fallback)
            self.stats.pending_peak = max(self.stats.pending_peak, len(self._pending))
            if self._thread is None:
                self._thread = Thread(target=self._run, name="hub-render", daemon=True)
                self._thread.start()
            self._condition.notify()

    def _deliver(self, slot, generation, deliver, result):
        with self._condition:
            current = not self._closed and self._generation[slot] == generation
        if current:
            deliver(result)

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                slot = next(iter(self._pending))
                generation, loop, build, deliver, fallback = self._pending.pop(slot)
            started = perf_counter()
            try:
                result = build()
            except Exception as error:
                self.stats.failures += 1
                try:
                    result = fallback(error)
                except Exception:
                    continue
            finally:
                self.stats.seconds += perf_counter() - started
                self.stats.rendered += 1
            with self._condition:
                current = not self._closed and self._generation[slot] == generation
            if current:
                try:
                    loop.call_soon_threadsafe(self._deliver, slot, generation, deliver, result)
                except RuntimeError:  # Host prompt loop was closed while hidden.
                    pass
            else:
                self.stats.discarded += 1

    def close(self):
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify()
