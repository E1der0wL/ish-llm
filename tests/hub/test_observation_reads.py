"""Observation work is event-driven; caches never become execution authority."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hub.backend.observability.reads import ObservationReads, ReadKind
from hub.backend.observability.snapshot import SnapshotReader
from hub.backend.runtime import HubRuntime
from hub.backend.worker import BackendWorker
from hub.locales.keys import MessageKey
from llm.core.models import Message
from llm.core.results import EngineDelta, OutputVisibility
from llm.engines.events import EngineEvent, EngineEventType as Event


class ObservationReadTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        self.reader = SnapshotReader()
        self.record = NS(id='s', title='Session', agent_ids=['assistant'], metadata={})
        self.run = NS(id='r', session_id='s', status='running', engine='loop',
                      started_at=None, ended_at=None, error=None, metadata={'completions': []})
        self.handle = NS(id='r', aget_data=AsyncMock(side_effect=lambda: self.run),
                         steps=NS(alist=AsyncMock(return_value=[])))
        self.status = NS(status='running', queued_count=0, active_runs=[NS(run_id='r')])
        self.message = Message(id='a', role='assistant', content='hello', status='streaming', run_id='r')
        self.session = NS(aget_data=AsyncMock(side_effect=lambda: self.record),
                          aconversation=AsyncMock(side_effect=lambda: [self.message]),
                          run=NS(astatus=AsyncMock(return_value=self.status),
                              alist=AsyncMock(side_effect=lambda *, query: [] if query.status else [self.handle]),
                              aload=AsyncMock(return_value=self.handle)))
        self.project = NS(aget_data=AsyncMock(return_value=NS(id='p', title='P', config={},
                             components=[], conversation_storage='file')), paths=NS(root='/workspace'),
                          agents=NS(alist=AsyncMock(return_value={})))
        self.config = NS(agent_id='assistant', file_root=None, user_profile=NS(display_name='Account'))
        self.engines = NS(names=lambda: ('loop',), get=lambda _: NS())

    async def read(self):
        return (await self.reader.read(self.project, {'s': self.session}, 's',
            config=self.config, engines=self.engines, title_errors={}))[0]

    async def test_100_unchanged_frames_read_selected_history_only_once(self):
        self.fixture()
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            for _ in range(100):
                snapshot = await self.read()
        self.assertEqual(snapshot.messages[-1].text, 'hello')
        self.session.run.astatus.assert_awaited_once()
        self.assertEqual(self.session.run.alist.await_count, 2)  # latest + paused
        self.session.aconversation.assert_awaited_once()
        self.handle.aget_data.assert_awaited_once()
        self.handle.steps.alist.assert_awaited_once()
        self.project.aget_data.assert_awaited_once()
        self.project.agents.alist.assert_awaited_once()

    async def test_deltas_refresh_only_visible_history_and_steps_refresh_on_events(self):
        self.fixture()
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            await self.read()
            for index in range(100):
                self.reader.observe_engine(self.run, EngineEvent(Event.TEXT_DELTA,
                    delta=EngineDelta(output_id='o', text='x', sequence=index + 1,
                                      visibility=OutputVisibility.INTERNAL)))
                await self.read()
            self.session.aconversation.assert_awaited_once()
            self.message = replace(self.message, content='updated')
            self.reader.observe_engine(self.run, EngineEvent(Event.TEXT_DELTA,
                delta=EngineDelta(output_id='user', text='updated', visibility=OutputVisibility.USER)))
            self.assertEqual((await self.read()).messages[-1].text, 'updated')
            self.assertEqual(self.session.aconversation.await_count, 2)
            self.reader.observe_engine(self.run, EngineEvent(Event.STEP_STARTED, step_id='tool'))
            await self.read()
        self.session.run.astatus.assert_awaited_once()
        self.assertEqual(self.session.run.alist.await_count, 2)
        self.assertEqual(self.handle.steps.alist.await_count, 2)
        self.assertEqual(self.handle.aget_data.await_count, 1)  # Persisted event already supplied the Run.

    async def test_committed_run_event_reuses_handle_without_a_storage_read(self):
        self.fixture()
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            await self.read()
            changed = NS(**{**vars(self.run), 'status': 'completed', 'error': None})
            self.reader.observe_run(changed)
            _, record = await self.reader.read_run(self.session, 's', 'r')
            self.assertEqual(record.status, 'completed')
            self.handle.aget_data.assert_awaited_once()
            self.session.run.aload.assert_not_awaited()
            self.reader.observe_engine(self.run, EngineEvent(Event.STEERING_CHANGED))
            _, record = await self.reader.read_run(self.session, 's', 'r')
            self.assertEqual(record.status, 'completed')
            changed.status = 'failed'
            self.assertEqual(record.status, 'completed')  # Observation is an owned copy.
            self.reader.invalidate_dropped_events()
            await self.reader.read_run(self.session, 's', 'r')
            self.assertEqual(self.handle.aget_data.await_count, 2)

    async def test_event_during_service_read_is_published_without_regression(self):
        cache, entered, release = ObservationReads(), asyncio.Event(), asyncio.Event()
        key = (ReadKind.RUN, 's', 'r')
        async def load():
            entered.set()
            await release.wait()
            return 'old'
        pending = asyncio.create_task(cache.read(key, load))
        await entered.wait()
        cache.remember(key, 'committed event')
        release.set()
        self.assertEqual(await pending, 'committed event')
        fresh = AsyncMock()
        self.assertEqual(await cache.read(key, fresh), 'committed event')
        fresh.assert_not_awaited()

    async def test_queue_status_is_read_after_a_slow_history_query(self):
        self.fixture()
        self.status = NS(status='idle', queued_count=1, active_runs=[])
        async def history(*, query):
            if query.status:
                return []
            self.status = NS(status='running', queued_count=0, active_runs=[NS(run_id='r')])
            return [self.handle]
        self.session.run.astatus.side_effect = lambda **kwargs: self.status
        self.session.run.alist.side_effect = history
        snapshot = await self.read()
        self.assertEqual(snapshot.queued_count, 0)
        self.assertTrue(snapshot.run.active)
        self.session.run.astatus.assert_awaited_once()

    async def test_lifecycle_interaction_loss_and_expiry_reconcile(self):
        self.fixture()
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            await self.read()
            self.reader.observe_engine(self.run, EngineEvent(Event.INTERACTION_CHANGED))
            await self.read()
            self.assertEqual(self.session.run.astatus.await_count, 2)
            self.run.status = self.message.status = 'completed'
            self.status.active_runs = []
            self.status.status = 'idle'
            self.reader.observe_run(self.run)
            self.assertEqual((await self.read()).messages[-1].status, 'completed')
            self.reader.invalidate_dropped_events()
            await self.read()
            self.assertEqual(self.session.run.astatus.await_count, 4)
        with patch('hub.backend.observability.reads.monotonic', return_value=103):
            await self.read()
        self.assertEqual(self.session.run.astatus.await_count, 5)
        self.assertEqual(self.project.agents.alist.await_count, 3)

    async def test_invalidated_inflight_read_cannot_restore_stale_cache(self):
        cache, entered, release = ObservationReads(), asyncio.Event(), asyncio.Event()
        key = (ReadKind.CONVERSATION, 's')
        async def load():
            entered.set()
            await release.wait()
            return 'old'
        pending = asyncio.create_task(cache.read(key, load))
        await entered.wait()
        cache.discard(*key)
        release.set()
        self.assertEqual(await pending, 'old')
        fresh = AsyncMock(return_value='new')
        self.assertEqual(await cache.read(key, fresh), 'new')
        fresh.assert_awaited_once()

    async def test_finished_message_cannot_publish_stale_active_recipients(self):
        self.fixture()
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            await self.read()
            # The final conversation commit is visible before its queued Run
            # callback. The previous runtime observation still says running.
            self.message = replace(self.message, content='done', status='completed')
            self.run = NS(**{**vars(self.run), 'status': 'completed'})
            self.session.run.astatus.return_value = NS(status='idle', queued_count=0, active_runs=[])
            self.reader.reads.discard(ReadKind.CONVERSATION, 's')
            snapshot = await self.read()
        self.assertEqual(snapshot.messages[-1].status, 'completed')
        self.assertFalse(snapshot.active_run_ids)
        self.assertFalse(snapshot.run.active)
        self.assertEqual(snapshot.status, 'idle')
        self.assertEqual(self.session.run.astatus.await_count, 2)

    async def test_failed_reads_and_eviction_never_cache_partial_results(self):
        cache = ObservationReads()
        key = (ReadKind.RUN, 's', 'r')
        for error in (ValueError('read failed'), asyncio.CancelledError()):
            with self.assertRaises(type(error)):
                await cache.read(key, AsyncMock(side_effect=error))
            self.assertNotIn(key, cache.entries)
        cache.LIMIT = 2
        for i in range(3):
            await cache.read((ReadKind.RUN, 's', i), AsyncMock(return_value=i))
        self.assertEqual(len(cache.entries), 2)

    async def test_wait_stage_only_when_command_really_waits(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker._initialized = asyncio.Event()
        worker._initialized.set()
        worker._closed = worker._restarting = False
        worker._failure = None
        worker._commands = set()
        worker._snapshot_task = None
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(),
                            settings=AsyncMock(return_value='settings'))
        updates = []
        await worker._execute('settings', (), stage=updates.append)
        self.assertNotIn(MessageKey.PROGRESS_BACKEND_WAIT, [u.key for u in updates])
        await worker.runtime.lock.acquire()
        updates.clear()
        pending = asyncio.create_task(worker._execute('settings', (), stage=updates.append))
        await asyncio.sleep(0)
        self.assertEqual([u.key for u in updates], [MessageKey.PROGRESS_BACKEND_WAIT])
        worker.runtime.lock.release()
        await pending

    async def test_select_previously_open_session_does_not_repeat_recovery(self):
        runtime = HubRuntime.__new__(HubRuntime)
        runtime.sessions = {'s': NS(run=NS(start=AsyncMock()))}
        runtime._opened_sessions = set()
        runtime.dirty = asyncio.Event()
        await runtime.select('s')
        await runtime.select('s')
        runtime.sessions['s'].run.start.assert_awaited_once()
        self.assertEqual(runtime.selected_id, 's')

    async def test_slow_snapshot_counts_toward_rate_limit_and_preserves_pending_change(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker._restarting = False
        worker._output_interval = .1
        worker._visible = True
        worker._interval_changed = asyncio.Event()
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event())
        worker.runtime.dirty.set()
        released, second = asyncio.Event(), asyncio.Event()
        calls = []
        async def snapshot():
            calls.append(asyncio.get_running_loop().time())
            if len(calls) == 1:
                await asyncio.sleep(.15)
                worker.runtime.dirty.set()
                released.set()
            else:
                second.set()
            return None
        worker.runtime.snapshot = snapshot
        worker.publish = Mock()
        worker.report_error = Mock()
        task = asyncio.create_task(worker._refresh())
        try:
            async with asyncio.timeout(3):
                await released.wait()
                await second.wait()
            # The change arrived during the read. Its duration already consumed
            # the frame interval; do not add another full delay after finishing.
            self.assertGreaterEqual(calls[1] - calls[0], worker._output_interval)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        worker.report_error.assert_not_called()
