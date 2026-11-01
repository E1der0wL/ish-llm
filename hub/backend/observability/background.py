"""One bounded lane for non-selected Session observations, never execution."""

import asyncio
from collections import OrderedDict


class BackgroundObservations:
    LIMIT = 1024

    def __init__(self, changed):
        self.changed = changed
        self.pending = OrderedDict()
        self.task = None
        self.tasks = set()
        self.error = None

    def clear(self):
        self.pending.clear()
        self.error = None
        if self.task is not None:
            self.task.cancel()
            self.task = None

    async def close(self):
        self.clear()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def check(self):
        if self.error is not None:
            error, self.error = self.error, None
            raise error

    def request(self, identifier, load):
        self.pending[identifier] = load
        while len(self.pending) > self.LIMIT:
            self.pending.popitem(last=False)
        if self.task is None:
            self.task = asyncio.create_task(self._read())
            self.tasks.add(self.task)
            self.task.add_done_callback(self.tasks.discard)

    async def _read(self):
        current = asyncio.current_task()
        try:
            while self.pending and self.task is current:
                _, load = self.pending.popitem(last=False)
                await load()
                if self.task is current:
                    self.changed()
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self.task is current:
                self.error = error
                self.changed()
        finally:
            if self.task is current:
                self.task = None
