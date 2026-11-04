"""Request redraws at the configured clock boundary while active."""

import asyncio

from .config import ClockSchedule

class ClockRuntime:
    def __init__(self, invalidate):
        self._invalidate = invalidate

    async def run(self, schedule: ClockSchedule):
        while True:
            now = schedule.clock.now()
            await asyncio.sleep(schedule.next_tick(now))
            self._invalidate()
