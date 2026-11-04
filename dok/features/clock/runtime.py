"""Request redraws at the configured clock boundary while active."""

import asyncio


class ClockRuntime:
    def __init__(self, invalidate):
        self._invalidate = invalidate

    async def run(self, config, date, time):
        while True:
            now = config.now()
            await asyncio.sleep(config.next_tick(now, date, time))
            self._invalidate()
