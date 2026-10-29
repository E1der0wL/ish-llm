"""Work-count and event-boundary regressions for constrained hosts."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hub.backend.observability.snapshot import SnapshotReader
from hub.backend.project.naming import SessionNamer
from hub.backend.project.settings_contracts import SettingsOperation
from hub.backend.worker import BackendWorker
from hub.config.sections import PreferenceSection
from hub.locales import Language
from hub.ui.live import LiveHubView
from hub.ui.presentation import MessagePresentationCache
from llm.core.models import Message
from llm.core.results import EngineDelta, OutputVisibility
from llm.engines.events import EngineEvent, EngineEventType as Event
from tests.hub import test_observation_reads as observations


class LowResourceObservationTests(unittest.IsolatedAsyncioTestCase):
    fixture = observations.ObservationReadTests.fixture
    read = observations.ObservationReadTests.read

    async def test_idle_ticks_reuse_projection_and_visible_delta_refreshes_it(self):
        self.fixture()
        presentation, language = MessagePresentationCache(), Language('ko')
        with patch('hub.backend.observability.reads.monotonic', return_value=100), \
                patch.object(self.reader, 'messages', wraps=self.reader.messages) as build, \
                patch.object(self.reader, 'run_display', wraps=self.reader.run_display) as evidence:
            first = await self.read()
            display = presentation.messages(first, language)
            for _ in range(100):
                current = await self.read()
                self.assertIs(current.messages, first.messages)
                self.assertIs(presentation.messages(current, language), display)
            build.assert_awaited_once()
            evidence.assert_awaited_once()  # Summary clocks never reparse completion metadata.
            self.message = replace(self.message, content='new answer')
            self.reader.observe_engine(self.run, EngineEvent(Event.TEXT_DELTA,
                delta=EngineDelta(output_id='answer', text='new answer', visibility=OutputVisibility.USER)))
            current = await self.read()
            self.assertEqual(current.messages[-1].text, 'new answer')
            self.assertEqual(build.await_count, 2)
            self.assertIsNot(presentation.messages(current, language), display)

    async def test_settled_history_reuses_rows_and_deletion_prunes_cache(self):
        reader = SnapshotReader()
        history = [Message(id=f'u{i}', role='user', content=f'question {i}', status='completed')
                   for i in range(1000)]
        active = Message(id='a', role='assistant', content='a', status='streaming')
        first = await reader.messages(None, [*history, active])
        for i in range(20):
            current = await reader.messages(None, [*history, replace(active, content=str(i))])
            self.assertTrue(all(a is b for a, b in zip(first[:-1], current[:-1])))
        changed = replace(history[0], content='edited')
        current = await reader.messages(None, [changed, active])
        self.assertEqual(current[0].text, 'edited')
        self.assertEqual(set(reader.projection.settled), {'u0'})
        reader.invalidate_dropped_events()
        self.assertFalse(reader.projection.settled)

    async def test_nonselected_session_expiry_and_events(self):
        self.fixture()
        async def background():
            return await self.reader.read(self.project, {'s': self.session}, '',
                config=self.config, engines=self.engines, title_errors={})
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            await background()
        with patch('hub.backend.observability.reads.monotonic', return_value=103):
            await background()
            self.session.run.astatus.assert_awaited_once()
            self.reader.observe_run(self.run)
            await background()
            self.assertEqual(self.session.run.astatus.await_count, 2)
        with patch('hub.backend.observability.reads.monotonic', return_value=114):
            await background()
            self.assertEqual(self.session.run.astatus.await_count, 3)

    async def test_title_check_once_per_source_and_configuration(self):
        runtime = NS(config=NS(auto_title=True), dirty=asyncio.Event())
        namer = SessionNamer(runtime)
        namer._name = AsyncMock()
        session = NS(id='s')
        namer.observe_configuration('p', {'assistant': {'completion': {'model': 'one'}}})
        for _ in range(100):
            namer.schedule(session, 'r1')
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        namer._name.assert_awaited_once()
        namer.observe_configuration('p', {'assistant': {'completion': {'model': 'two'}}})
        namer.schedule(session, 'r1')
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        namer.schedule(session, 'r2')
        await asyncio.sleep(0)
        self.assertEqual(namer._name.await_count, 3)
        await namer.close()

    async def test_cancelled_title_check_can_be_scheduled_again(self):
        namer = SessionNamer(NS(config=NS(auto_title=True), dirty=asyncio.Event()))
        namer._name = AsyncMock(side_effect=asyncio.CancelledError)
        for _ in range(2):
            namer.schedule(NS(id='s'), 'r')
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        self.assertEqual(namer._name.await_count, 2)

    async def test_already_attempted_title_does_not_dirty_the_observer(self):
        from hub.backend.project.title_values import TitleAttempt, TitleSessionMetadata
        runtime = NS(config=NS(auto_title=True), dirty=asyncio.Event(), snapshot_reader=Mock())
        namer = SessionNamer(runtime)
        parameters = {'model': 'test/model'}
        namer._completion = AsyncMock(return_value=parameters)
        metadata = TitleSessionMetadata({}).initialize(True)
        metadata = TitleSessionMetadata(metadata).with_attempt(TitleAttempt.digest('r', parameters))
        handle = NS(aget_data=AsyncMock(return_value=NS(id='r')))
        session = NS(id='s', aget_data=AsyncMock(return_value=NS(metadata=metadata)),
            aconversation=AsyncMock(return_value=[
                Message(id='u', role='user', status='completed', content='Inspect the configuration'),
                Message(id='a', role='assistant', status='completed', content='Inspected')]),
            run=NS(alist=AsyncMock(return_value=[handle])))
        await namer._name(session, 'r')
        self.assertFalse(runtime.dirty.is_set())
        runtime.snapshot_reader.invalidate.assert_not_called()

    async def test_hidden_updates_coalesce_and_urgent_event_wakes_observer(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker._visible, worker._restarting = False, False
        worker._output_interval = .01
        worker.HIDDEN_INTERVAL = 60  # No timing race: only an urgent event can wake this.
        worker._interval_changed = asyncio.Event()
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), urgent_observation=False)
        worker.runtime.snapshot = AsyncMock(return_value=NS(run=None, queued_count=0, sessions=()))
        worker.publish, worker.report_error = Mock(), Mock()
        task = asyncio.create_task(worker._refresh())
        try:
            for _ in range(20):
                worker.runtime.dirty.set()
                await asyncio.sleep(0)
            worker.runtime.snapshot.assert_not_awaited()
            worker.runtime.urgent_observation = True
            worker.runtime.dirty.set()
            async with asyncio.timeout(3):
                while not worker.publish.called:
                    await asyncio.sleep(.01)
            worker.runtime.snapshot.assert_awaited_once()
            worker.report_error.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_builtin_question_is_urgent_even_without_a_paused_run(self):
        from hub.backend.runtime import HubRuntime
        runtime = HubRuntime.__new__(HubRuntime)
        runtime.builtin_tools = NS(questions=NS(pending={}))
        runtime.notifications = Mock()
        runtime.dirty = asyncio.Event()
        runtime.urgent_observation = False
        runtime._questions_changed()
        self.assertTrue(runtime.urgent_observation)
        self.assertTrue(runtime.dirty.is_set())

    async def test_lifecycle_event_preempts_old_frame_but_drains_its_read(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker.runtime = NS(dirty=asyncio.Event(), urgent_observation=False)
        entered, cancelled, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def read():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
                await drained.wait()  # Same cancellation contract as a public storage call.
        worker._snapshot_task = asyncio.create_task(read())
        pending = asyncio.create_task(worker._await_snapshot())
        try:
            await entered.wait()
            worker.runtime.urgent_observation = True
            worker.runtime.dirty.set()
            async with asyncio.timeout(3):
                await cancelled.wait()
            self.assertFalse(pending.done())
            drained.set()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertTrue(worker.runtime.dirty.is_set())
        finally:
            drained.set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_normal_delta_does_not_cancel_snapshot_and_refresh_still_follows(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker.runtime = NS(dirty=asyncio.Event(), urgent_observation=False)
        release = asyncio.Event()
        async def read():
            await release.wait()
            return 'frame'
        worker._snapshot_task = asyncio.create_task(read())
        pending = asyncio.create_task(worker._await_snapshot())
        try:
            worker.runtime.dirty.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(worker._snapshot_task.cancelled())
            release.set()
            self.assertEqual(await pending, 'frame')
            self.assertTrue(worker.runtime.dirty.is_set())
        finally:
            release.set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_repeated_lifecycle_changes_cannot_starve_the_next_frame(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker._visible, worker._restarting = True, False
        worker._output_interval = .001
        worker._interval_changed = asyncio.Event()
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), urgent_observation=False)
        entered, published = asyncio.Event(), asyncio.Event()
        attempts = []
        frame = NS(run=None, queued_count=0, sessions=())
        async def snapshot():
            attempts.append(None)
            if len(attempts) == 1:
                entered.set()
                await asyncio.Future()
            worker.runtime.urgent_observation = True
            worker.runtime.dirty.set()
            await asyncio.sleep(.01)
            return frame
        worker.runtime.snapshot = snapshot
        worker.publish = lambda value: published.set()
        worker.report_error = Mock()
        task = asyncio.create_task(worker._refresh())
        try:
            async with asyncio.timeout(3):
                await entered.wait()
                worker.runtime.urgent_observation = True
                worker.runtime.dirty.set()
                await published.wait()
            self.assertEqual(len(attempts), 2)
            worker.report_error.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_usage_is_lazy_nonblocking_and_updates_chart(self):
        view = LiveHubView()
        screen, calls = view.settings, []
        screen.schema = {}
        screen.request = lambda operation, args, callback: calls.append((operation, callback))
        try:
            screen.drafts.preview_preferences()
            screen.choose(PreferenceSection.GENERAL.value)
            self.assertFalse(calls)
            screen.choose(PreferenceSection.PROFILE.value)
            self.assertEqual([op for op, _ in calls], [SettingsOperation.USAGE])
            self.assertFalse(screen.busy)
            screen.choose(PreferenceSection.GENERAL.value)
            from hub.backend.project.settings_contracts import ProjectUsageRecord
            record = ProjectUsageRecord.collect(NS(id='p', title='P'),
                {'calls': [], 'known_tokens': 120, 'call_count': 1,
                 'reserved_tokens': 0, 'unknown_calls': 0, 'period_seconds': None}).to_dict()
            record['daily'] = [{'date': '2026-10-28', 'tokens': 120, 'unknown': 0}]
            calls[0][1]([record])
            page = screen.drafts.pages[PreferenceSection.PROFILE.value]
            self.assertEqual(page.usage_chart.content.days['2026-10-28'].tokens, 120)
            screen.choose(PreferenceSection.PROFILE.value)
            self.assertEqual(len(calls), 1)
        finally:
            view.close()
