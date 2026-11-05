"""Export I/O cannot block observation production; overflow is explicit."""

import asyncio
from threading import Event
import unittest

from llm.services.infrastructure.exporter import QueuedSink
from llm.services.infrastructure.observability import Observability


class ExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_queue_explicit_overflow_and_drain(self):
        for overflow, expected in (("drop_newest", [1, 2]), ("drop_oldest", [1, 3])):
            with self.subTest(overflow=overflow):
                entered, release = Event(), Event()
                received = []
                def export(value):
                    received.append(value["number"])
                    if value["number"] == 1:
                        entered.set()
                        if not release.wait(5):
                            raise TimeoutError("fixture exporter was not released")
                sink = QueuedSink(export, capacity=1, overflow=overflow)
                try:
                    sink({"number": 1})
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    value = {"number": 2}
                    sink(value)
                    value["number"] = 99
                    sink({"number": 3})
                    self.assertEqual(sink.snapshot()["dropped"], 1)
                finally:
                    release.set()
                    await sink.aclose()
                self.assertEqual(received, expected)
                self.assertEqual(sink.snapshot()["exported"], 2)
                sink({"number": 4})
                self.assertEqual(sink.snapshot()["rejected"], 1)

    async def test_cancelled_close_drains_and_observation_counters_stay_owned(self):
        entered, release = Event(), Event()
        def export(value):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("fixture exporter was not released")
            raise OSError("fixture sink unavailable")
        sink = QueuedSink(export, capacity=2, overflow="drop_newest")
        observer = Observability(sink=sink)
        try:
            observer.record("tools", "requests")
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            observer.record("tools", "completed")
            self.assertEqual(observer.snapshot()["tools"]["requests"], 1)
            closing = asyncio.create_task(sink.aclose())
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            self.assertEqual(sink.snapshot()["failed"], 2)
        finally:
            release.set()
            await sink.aclose()

    async def test_no_implicit_capacity_or_overflow(self):
        for values in ({}, {"capacity": 1}, {"overflow": "drop_oldest"},
                       {"capacity": 0, "overflow": "drop_oldest"},
                       {"capacity": True, "overflow": "drop_oldest"},
                       {"capacity": 1, "overflow": "block"}):
            with self.subTest(values=values), self.assertRaises((TypeError, ValueError)):
                QueuedSink(lambda event: None, **values)

    async def test_async_callback_is_not_silently_counted_as_exported(self):
        async def export(value):
            self.fail('async callback must not execute in the synchronous exporter')
        with self.assertRaisesRegex(TypeError, 'synchronous'):
            QueuedSink(export, capacity=1, overflow='drop_newest')
        sink = QueuedSink(lambda value: export(value), capacity=1, overflow='drop_newest')
        sink({})
        await sink.aclose()
        self.assertEqual(sink.snapshot()['exported'], 0)
        self.assertEqual(sink.snapshot()['failed'], 1)
