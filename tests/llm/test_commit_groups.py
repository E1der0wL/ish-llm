"""Durable groups share one decision, never an early execution acknowledgement."""

import asyncio
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from llm.core.checkpoint_records import CheckpointDeclarations, CheckpointWrite
from llm.core.steering_channel import SteeringChannel
from llm.engines.events import EngineEvent, EngineEventType
from llm.llm import LargeLanguageModel, LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.services.infrastructure.record_journal import RecordJournal
from llm.services.infrastructure.record_transactions import RecordTransaction
from llm.services.infrastructure.transactions import current_transaction
from llm.services.runtime.runs import RunManager
from tests.llm.test_loop import chunk


class CommitGroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.calls, self.events = [], []

        def completion(**kwargs):
            self.calls.append(kwargs)
            yield chunk('done', finish='stop')

        self.app = LargeLanguageModel(Path(folder.name), engines={
            'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=completion)),
        })
        self.addAsyncCleanup(self.app.shutdown)
        self.app.events.subscribe(lambda run, event: self.events.append(event), channel='engine')
        self.project = await self.app.projects.acreate('groups')
        await self.project.agents.acreate({
            'engine': 'loop', 'completion': {'model': 'test/model'},
        }, identifier='agent')
        self.session = await self.project.sessions.acreate(agent_ids=['agent'])

    async def execute(self):
        request = await self.session.run.submit('current request', recipients=['agent'])
        run, = await request.wait(timeout=15)
        await self.session.run.wait_idle()
        return run

    async def test_initial_checkpoint_and_channel_share_one_durable_commit(self):
        prepared, committed, syncs = [], [], []
        original = RunManager._steering_boundary
        commit, sync = RecordTransaction.commit, os.fsync

        def boundary(manager, run, store, event, usage):
            if event.has_checkpoint:
                prepared.append(current_transaction())
                self.assertFalse(CheckpointDeclarations.read(run.metadata).names)
            return original(manager, run, store, event, usage)

        def persist(transaction):
            result = commit(transaction)
            if transaction in prepared:
                committed.append(transaction)
            return result

        def fsync(fd):
            if current_transaction() in prepared:
                syncs.append(Path(os.readlink(f'/proc/self/fd/{fd}')).name)
            return sync(fd)

        with patch.object(RunManager, '_steering_boundary', boundary), \
                patch.object(RecordTransaction, 'commit', persist), patch.object(os, 'fsync', fsync):
            run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(prepared), 1)
        self.assertEqual(committed, prepared)
        self.assertEqual(sum(bool(RecordJournal.BUNDLE.fullmatch(p)) for p in syncs), 1)
        self.assertTrue(await run.acheckpoint())
        self.assertTrue(any(e.type == EngineEventType.CHECKPOINT for e in self.events))

    async def test_channel_failure_rolls_back_prepared_checkpoint_and_never_calls_model(self):
        from llm.services.runtime.steering import steering_boundary

        def fail(repository, run, store, event, **kwargs):
            result = steering_boundary(repository, run, store, event, **kwargs)
            if event.has_checkpoint:
                raise OSError('abort prepared channel')
            return result

        with patch('llm.services.runtime.runs.steering_boundary', fail):
            run = await self.execute()
        self.assertEqual(run.data.status, 'failed')
        self.assertIn('abort prepared channel', run.data.error)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(CheckpointDeclarations.read(run.data.metadata).names), [])
        channel = SteeringChannel.require(run.data)
        self.assertFalse(channel.to_dict()[SteeringChannel.Field.ROOT_OPENED])
        self.assertTrue(all(not target.accepting for target in channel.targets.values()))
        self.assertFalse(any(e.type == EngineEventType.CHECKPOINT for e in self.events))

    async def test_cancellation_drains_commit_without_starting_model(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        prepared = []
        original, commit = RunManager._steering_boundary, RecordTransaction.commit

        def boundary(manager, run, store, event, usage):
            if event.has_checkpoint:
                prepared.append(current_transaction())
            return original(manager, run, store, event, usage)

        def persist(transaction):
            if transaction in prepared:
                entered.set()
                if not release.wait(15):
                    raise TimeoutError('test commit gate')
            return commit(transaction)

        with patch.object(RunManager, '_steering_boundary', boundary), \
                patch.object(RecordTransaction, 'commit', persist):
            request = await self.session.run.submit('cancel', recipients=['agent'])
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 15))
                interrupted = asyncio.create_task(self.session.run.interrupt())
                # Give interrupt a scheduling turn; it must drain the accepted write.
                await asyncio.sleep(0)
            finally:
                release.set()
            self.assertTrue(await interrupted)
            run, = await request.wait(timeout=15)
        self.assertEqual(run.data.status, 'interrupted')
        self.assertEqual(self.calls, [])
        self.assertTrue(await run.acheckpoint())

    async def test_uncertain_fsync_never_calls_model_and_explicit_resume_reuses_checkpoint(self):
        prepared, failed = [], []
        original, sync = RunManager._steering_boundary, os.fsync

        def boundary(manager, run, store, event, usage):
            if event.has_checkpoint:
                prepared.append(current_transaction())
            return original(manager, run, store, event, usage)

        def fsync(fd):
            name = Path(os.readlink(f'/proc/self/fd/{fd}')).name
            if not failed and current_transaction() in prepared and RecordJournal.BUNDLE.fullmatch(name):
                failed.append(name)
                raise OSError('uncertain group fsync')
            return sync(fd)

        with patch.object(RunManager, '_steering_boundary', boundary), patch.object(os, 'fsync', fsync):
            run = await self.execute()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.calls, [])
        self.assertFalse(any(e.type == EngineEventType.CHECKPOINT for e in self.events))
        # A complete but unacknowledged frame is recovered before observation;
        # recovery does not repeat model/Tool execution.
        self.assertTrue(await run.acheckpoint())
        request = await self.session.run.resume(run.id, agent_id='agent')
        resumed, = await request.wait(timeout=15)
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(len(self.calls), 1)

    async def test_recovery_and_finish_each_use_one_record_commit(self):
        selected, committed = {}, []
        recover, finish, commit = RunManager._recover, RunManager._finish, RecordTransaction.commit

        def recovery(manager, session):
            selected['recovery'] = current_transaction()
            return recover(manager, session)

        def terminal(manager, *args):
            selected['finish'] = current_transaction()
            return finish(manager, *args)

        def persist(transaction):
            result = commit(transaction)
            if any(transaction is value for value in selected.values()):
                committed.append(transaction)
            return result

        with patch.object(RunManager, '_recover', recovery), \
                patch.object(RunManager, '_finish', terminal), patch.object(RecordTransaction, 'commit', persist):
            run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(selected), 2)
        for transaction in selected.values():
            self.assertIsInstance(transaction, RecordTransaction)
            self.assertEqual(committed.count(transaction), 1)
        self.assertEqual((await self.session.aget_data()).active_run_ids, [])
        self.assertEqual((await run.aresponse()).status, 'completed')

    async def test_recovery_failure_releases_attachment_and_never_schedules(self):
        original = RunManager._recover

        def fail(manager, session):
            original(manager, session)
            raise OSError('abort recovery')

        with patch.object(RunManager, '_recover', fail):
            with self.assertRaisesRegex(OSError, 'abort recovery'):
                await self.session.run.start()
        self.assertEqual(self.calls, [])
        self.assertEqual(await self.session.run.alist(), [])
        # A failed attachment must not strand ownership or the next start.
        run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)

    async def test_terminal_notifications_retire_records_without_detaching_session(self):
        settled = []

        def observe(event):
            if event.run.status == 'completed':
                journal = self.app.project_manager.ownership.transactions.records
                settled.append((event.run.id, len(journal.frames), len(journal.files)))

        self.app.events.subscribe(observe, channel='run')
        first, second = await self.execute(), await self.execute()
        self.assertEqual(settled, [(first.id, 0, 0), (second.id, 0, 0)])
        self.assertEqual(len(self.calls), 2)


class CheckpointAttachmentTests(unittest.TestCase):
    def test_initialization_is_not_a_completion_receipt_or_stream_delta(self):
        checkpoint = EngineEvent(EngineEventType.CHECKPOINT,
            metadata=CheckpointWrite.initialize_event('custom', {}).to_dict())
        for kind in (EngineEventType.STEP_STARTED, EngineEventType.STEERING):
            self.assertIs(EngineEvent(kind, checkpoint=checkpoint).checkpoint, checkpoint)
        for kind in (EngineEventType.STEP_COMPLETED, EngineEventType.TEXT_DELTA,
                     EngineEventType.CHECKPOINT, EngineEventType.OUTPUT):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                EngineEvent(kind, checkpoint=checkpoint)
