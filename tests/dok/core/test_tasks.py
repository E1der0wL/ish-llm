"""Task replacement preserves asynchronous cleanup and host lifetime."""
import asyncio
import inspect
import unittest

from dok.core.tasks import TaskSlot


class TaskSlotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = []
        self.changed = asyncio.Event()
        self.slot = TaskSlot(self.changed.set)

    async def asyncTearDown(self):
        self.slot.close()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def sync(self, run, *, key='first', active=True):
        return self.slot.sync(active=active, key=key, run=run, start=self.start)

    async def test_replacement_waits_for_cleanup_without_repeated_cancellation(self):
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []

        async def first():
            events.append('first started')
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                events.append('first cleaned')

        async def second():
            events.append('second started')
            await asyncio.Event().wait()

        self.assertTrue(self.sync(first))
        await entered.wait()
        self.assertTrue(self.sync(first))
        self.assertEqual(len(self.tasks), 1)
        self.assertFalse(self.sync(second, key='second'))
        await cleaning.wait()
        try:
            for active in (False, True, False, True):
                self.assertFalse(self.sync(second, key='second', active=active))
                await asyncio.sleep(0)
            self.assertEqual(len(self.tasks), 1)
            self.assertEqual(events, ['first started'])
        finally:
            release.set()
        await asyncio.gather(self.tasks[0], return_exceptions=True)
        await asyncio.wait_for(self.changed.wait(), 3)
        self.assertTrue(self.sync(second, key='second'))
        await asyncio.sleep(0)
        self.assertEqual(events, ['first started', 'first cleaned', 'second started'])

    async def test_close_is_final_and_completion_never_starts_a_task(self):
        started = asyncio.Event()

        async def run():
            started.set()
            await asyncio.Event().wait()

        self.sync(run)
        await started.wait()
        self.slot.close()
        self.slot.close()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.assertFalse(self.changed.is_set())
        self.assertFalse(self.sync(run))
        self.assertEqual(len(self.tasks), 1)

    async def test_host_cancellation_requires_explicit_sync_to_restart(self):
        async def run():
            await asyncio.Event().wait()

        self.sync(run)
        await asyncio.sleep(0)
        self.tasks[0].cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.wait_for(self.changed.wait(), 3)
        self.assertEqual(len(self.tasks), 1)
        self.assertFalse(self.sync(run, active=False))
        self.assertTrue(self.sync(run))
        self.assertEqual(len(self.tasks), 2)

    async def test_rejected_task_creation_closes_coroutine_and_can_retry(self):
        captured = []

        async def run():
            await asyncio.Event().wait()

        def reject(coroutine):
            captured.append(coroutine)
            raise RuntimeError('host stopped')

        with self.assertRaisesRegex(RuntimeError, 'host stopped'):
            self.slot.sync(active=True, key=None, run=run, start=reject)
        self.assertEqual(inspect.getcoroutinestate(captured[0]), inspect.CORO_CLOSED)
        self.assertTrue(self.sync(run))
