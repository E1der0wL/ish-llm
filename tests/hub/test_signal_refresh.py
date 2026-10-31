"""Change-driven observations preserve trailing frames, loss recovery and rate limits."""

import asyncio
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

from hub.backend.worker import BackendWorker
from hub.backend.runtime import HubRuntime
from hub.backend.execution.retries import RetryMonitor
from hub.model import RetrySummary


class SignalRefreshTests(IsolatedAsyncioTestCase):
    def worker(self, snapshot, *, interval=.04):
        worker = BackendWorker.__new__(BackendWorker)
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), snapshot=snapshot)
        worker._restarting = worker._closed = False
        worker._snapshot_task = None
        worker._visible = True
        worker._output_interval = interval
        worker._interval_changed = asyncio.Event()
        worker.loop = asyncio.get_running_loop()
        worker.publish, worker.report_error = Mock(), Mock()
        return worker

    async def until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.001)

    async def stop(self, task, worker):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        worker.report_error.assert_not_called()

    async def test_first_frame_is_immediate_even_with_long_interval(self):
        async def snapshot():
            return 'first'
        worker = self.worker(snapshot, interval=10)
        task = asyncio.create_task(worker._refresh())
        try:
            await self.until(lambda: worker.publish.called)
            worker.publish.assert_called_once_with('first')
        finally:
            await self.stop(task, worker)

    async def test_quiet_active_run_does_not_poll_at_frame_rate(self):
        async def snapshot():
            return NS(run=NS(active=True), queued_count=1, sessions=())
        worker = self.worker(snapshot, interval=.01)
        task = asyncio.create_task(worker._refresh())
        try:
            await self.until(lambda: worker.publish.called)
            await asyncio.sleep(.12)
            self.assertEqual(worker.snapshot_count, 1)
            worker.runtime.dirty.set()
            await self.until(lambda: worker.snapshot_count == 2)
            await asyncio.sleep(.04)
            self.assertEqual(worker.snapshot_count, 2)
        finally:
            await self.stop(task, worker)

    async def test_burst_is_coalesced_and_final_value_is_not_lost(self):
        value, read_times = [0], []
        async def snapshot():
            read_times.append(asyncio.get_running_loop().time())
            return value[0]
        worker = self.worker(snapshot)
        task = asyncio.create_task(worker._refresh())
        try:
            await self.until(lambda: worker.publish.called)
            for i in range(1, 101):
                value[0] = i
                worker.runtime.dirty.set()
            await self.until(lambda: worker.publish.call_args.args == (100,))
            self.assertEqual(worker.snapshot_count, 2)
            self.assertGreaterEqual(read_times[1] - read_times[0], worker._output_interval - .002)
            await asyncio.sleep(.05)
            self.assertEqual(worker.snapshot_count, 2)
        finally:
            await self.stop(task, worker)

    async def test_event_during_read_is_reconciled_after_that_frame(self):
        entered, release = asyncio.Event(), asyncio.Event()
        value = [0]
        async def snapshot():
            captured = value[0]
            if not entered.is_set():
                entered.set()
                await release.wait()
            return captured
        worker = self.worker(snapshot)
        task = asyncio.create_task(worker._refresh())
        try:
            await entered.wait()
            value[0] = 1
            worker.runtime.dirty.set()
            await asyncio.sleep(0)
            release.set()
            await self.until(lambda: worker.publish.call_args and worker.publish.call_args.args == (1,))
        finally:
            await self.stop(task, worker)

    async def test_external_reconciliation_is_separate_from_streaming_interval(self):
        value = [0]
        async def snapshot():
            return value[0]
        worker = self.worker(snapshot, interval=.01)
        worker.RECONCILE_INTERVAL = .1
        task = asyncio.create_task(worker._refresh(initial=0))
        try:
            await asyncio.sleep(.04)
            worker.publish.assert_not_called()
            value[0] = 1  # A public API edit outside the local event stream.
            await self.until(lambda: worker.publish.call_args and worker.publish.call_args.args == (1,))
            self.assertEqual(worker.snapshot_count, 1)
        finally:
            await self.stop(task, worker)

    def test_provider_retry_changes_signal_without_waiting_for_engine_event(self):
        monitor = RetryMonitor()
        monitor.changed = Mock()
        summary = RetrySummary(1, 4, 100)
        monitor.set('call', 'run', summary)
        monitor.changed.assert_called_once_with()
        monitor.set('call', 'run', summary)
        self.assertEqual(monitor.changed.call_count, 1)
        monitor.set('call', 'run', RetrySummary(2, 4, 105))
        self.assertEqual(monitor.changed.call_count, 2)

    async def test_checkpoint_receipt_does_not_rebuild_visible_conversation(self):
        from llm.engines.events import EngineEvent, EngineEventType
        from llm.core.checkpoint_records import CheckpointWrite

        runtime = HubRuntime.__new__(HubRuntime)
        runtime.dirty = asyncio.Event()
        runtime.snapshot_reader = Mock()
        event = EngineEvent(EngineEventType.CHECKPOINT,
                           metadata=CheckpointWrite.record_event('loop', 'key', {}).to_dict())
        await runtime._engine_event(NS(engine='loop'), event)
        runtime.snapshot_reader.observe_engine.assert_not_called()
        self.assertFalse(runtime.dirty.is_set())
