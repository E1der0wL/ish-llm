"""Durable groups share one decision, never an early execution acknowledgement."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from dataclasses import replace
from contextlib import asynccontextmanager
from unittest.mock import patch

from llm.core.checkpoint_records import CheckpointDeclarations, CheckpointWrite
from llm.core.steering_channel import SteeringChannel
from llm.core.steering_events import SteeringEventData
from llm.core.checkpoint_records import CheckpointRecords
from llm.core.results import CompletionResult, CompletionRecords
from llm.engines.base import BaseEngine, EngineContext
from llm.policies import ExecutionLimitError
from llm.components.processing import CompletionPipeline
from llm.engines.events import EngineEvent, EngineEventType
from llm.engines.contracts import EngineHooks
from llm.providers.streaming import CompletionAdapterContract
from llm.llm import LargeLanguageModel, LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.services.infrastructure.record_journal import RecordJournal
from llm.services.infrastructure.record_transactions import RecordTransaction
from llm.services.infrastructure.transactions import current_transaction
from llm.services.runtime.runs import RunManager
from tests.llm.test_loop import chunk


class PreparedStepTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return EngineContext(project=None, session=None, run=SimpleNamespace(id='unit-run'), messages=())

    async def test_effect_waits_for_combined_start_ack_and_close_does_not_execute(self):
        effects = []

        async def work(context):
            yield EngineEvent(EngineEventType.COMPLETION, completion=CompletionResult())
            effects.append('called')

        stream = BaseEngine().step(self.context(), work, name='model', prepare=True)
        event = await anext(stream)
        self.assertTrue(event.has_completion_start)
        self.assertEqual(effects, [])
        await stream.aclose()
        self.assertEqual(effects, [])

    async def test_external_hook_fence_starts_step_before_hook(self):
        effects = []

        async def work(context):
            yield BaseEngine.execution_boundary()
            effects.append('hook')
            yield EngineEvent(EngineEventType.COMPLETION, completion=CompletionResult())

        stream = BaseEngine().step(self.context(), work, name='model', prepare=True, timeout_seconds=0.01)
        start = await anext(stream)
        self.assertEqual(start.type, EngineEventType.STEP_STARTED)
        self.assertFalse(start.has_completion_start)
        self.assertEqual(effects, [])
        # Waiting for a separate Step-start ACK remains outside action time.
        await asyncio.sleep(0.03)
        reservation = await anext(stream)
        self.assertEqual(reservation.type, EngineEventType.COMPLETION)
        self.assertEqual(effects, ['hook'])
        await stream.aclose()

    async def test_timeout_still_includes_preparation(self):
        async def work(context):
            await asyncio.Event().wait()

        stream = BaseEngine().step(self.context(), work, name='prepare', prepare=True, timeout_seconds=0.01)
        self.assertEqual((await anext(stream)).type, EngineEventType.STEP_STARTED)
        failure = await anext(stream)
        self.assertEqual(failure.type, EngineEventType.STEP_FAILED)
        self.assertEqual(failure.failure_code, 'step_timeout')
        with self.assertRaises(ExecutionLimitError) as caught:
            await anext(stream)
        self.assertEqual(caught.exception.code, 'step_timeout')

    async def test_cancel_preparation_does_not_create_a_started_step_or_effect(self):
        entered = asyncio.Event()

        async def work(context):
            entered.set()
            await asyncio.Event().wait()

        stream = BaseEngine().step(self.context(), work, name='prepare', prepare=True)
        task = asyncio.create_task(anext(stream))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await stream.aclose()

    async def test_root_binding_ack_stays_outside_agent_action_timeout(self):
        from llm.engines.agents import AgentInvocation

        class Child:
            async def execute(self, context):
                yield EngineEvent('extension')

        # Isolate the wrapper's timing contract; binding preparation has its own
        # persisted integration tests below and is not exercised by this fixture.
        invocation = AgentInvocation.__new__(AgentInvocation)
        invocation.runtime_binding = object()
        invocation.engine = Child()
        invocation.resources = None
        invocation.profile = SimpleNamespace(policy=SimpleNamespace(timeout_seconds=0.01),
                                             resources=SimpleNamespace(mcp={}))
        stream = invocation.execute(self.context())
        self.assertEqual((await anext(stream)).type, EngineEventType.EXECUTION_BOUNDARY)
        await asyncio.sleep(0.03)
        self.assertEqual((await anext(stream)).type, 'extension')
        await stream.aclose()


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

    async def test_initial_preparation_is_durable_before_model_invocation(self):
        journals, checked = [], []
        boundary = RunManager._steering_boundary
        adapter = self.app.engines.get('loop').completion_adapter
        original = adapter.completion_fn

        def prepared(manager, *args):
            journals.append(current_transaction().manager.records)
            return boundary(manager, *args)

        def completion(**kwargs):
            self.assertTrue(journals)
            self.assertEqual(journals[-1].prepared, [])
            checked.append(True)
            yield from original(**kwargs)

        with patch.object(RunManager, '_steering_boundary', prepared), patch.object(adapter, 'completion_fn', completion):
            run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(checked, [True])

    async def test_binding_is_prepared_but_undeclared_adapter_enters_after_ack(self):
        bindings, entries = [], []
        bind, execute = RunManager._bind_execution, LoopEngine.execute
        outer = self

        def capture(manager, *args, **kwargs):
            transaction = current_transaction()
            bindings.append(transaction)
            outer.assertFalse(transaction.durable)
            return bind(manager, *args, **kwargs)

        async def enter(engine, context):
            outer.assertTrue(bindings)
            outer.assertEqual(bindings[-1].manager.records.prepared, [])
            entries.append(True)
            async for event in execute(engine, context):
                yield event

        adapter = self.app.engines.get('loop').completion_adapter
        with patch.object(adapter, 'pure_preparation', False), \
                patch.object(RunManager, '_bind_execution', capture), patch.object(LoopEngine, 'execute', enter):
            run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(entries, [True])
        self.assertEqual(len(self.calls), 1)

    async def test_root_mcp_connection_waits_for_binding_durability(self):
        from llm.components.mcp import MCPComponent
        from llm.components.tools import ToolRegistry, Tool
        from tests.llm.support.runtime_tools import TEST_CONTRACT
        bindings, connected = [], []
        original = RunManager._bind_execution

        def capture(manager, *args, **kwargs):
            bindings.append(current_transaction().manager.records)
            return original(manager, *args, **kwargs)

        async def read(arguments):
            return 'unused'

        @asynccontextmanager
        async def connect(definition):
            self.assertTrue(bindings)
            self.assertEqual(bindings[-1].prepared, [])
            connected.append(True)
            yield ToolRegistry([Tool('read', 'read', {'type': 'object'}, read, contract=TEST_CONTRACT)])

        with tempfile.TemporaryDirectory() as folder:
            async with LargeLanguageModel(Path(folder), components=[MCPComponent(connector=connect)],
                    engines={'loop': self.app.engines.get('loop')}) as backend:
                project = await backend.projects.acreate(components=['mcp'])
                await project.components.mcp.acreate({'transport': 'streamable_http',
                    'url': 'https://example.invalid/mcp'}, identifier='remote')
                await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test/model'},
                    'resources': {'mcp': {'remote': {'read_remote': 'read'}}}}, identifier='mcp-agent')
                session = await project.sessions.acreate(agent_ids=['mcp-agent'])
                with patch.object(RunManager, '_bind_execution', capture):
                    request = await session.run.submit('inspect', recipients=['mcp-agent'])
                    run, = await request.wait(timeout=15)
                    await session.run.wait_idle()
                self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(connected, [True])

    def test_preparation_is_an_explicit_implementation_declaration(self):
        self.assertFalse(EngineHooks.prepares_execution(object()))
        self.assertFalse(CompletionAdapterContract.has_pure_preparation(object()))
        for method, field in ((EngineHooks.prepares_execution, 'prepares_execution'),
                              (CompletionAdapterContract.has_pure_preparation, 'pure_preparation')):
            for invalid in (None, 'true', 1):
                with self.subTest(field=field, value=invalid), self.assertRaises(TypeError):
                    method(SimpleNamespace(**{field: invalid}))

    async def test_model_start_and_reservation_commit_together_before_observers_and_call(self):
        groups, reservations, observed = [], [], []
        record, reserve = RunManager._record_engine_event, RunManager._record_completion
        outer = self

        def capture(manager, project, run, store, event):
            if event.has_completion_start:
                groups.append(current_transaction())
                outer.assertEqual(outer.calls, [])
            return record(manager, project, run, store, event)

        def reservation(manager, run, event):
            if event.completion.status == 'running':
                reservations.append(current_transaction())
            return reserve(manager, run, event)

        def observe(run, event):
            if event.type in (EngineEventType.STEP_STARTED, EngineEventType.COMPLETION):
                outer.assertTrue(groups)
                outer.assertEqual(groups[-1].manager.records.prepared, [])
                outer.assertTrue(CompletionRecords.results(run.metadata))
                observed.append(event)

        self.app.events.subscribe(observe, channel='engine')
        with patch.object(RunManager, '_record_engine_event', capture), \
                patch.object(RunManager, '_record_completion', reservation):
            run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(groups), 1)
        self.assertIs(groups[0], reservations[0])
        self.assertEqual([e.type for e in observed[:2]], [EngineEventType.STEP_STARTED, EngineEventType.COMPLETION])
        self.assertIsNone(observed[0].completion)
        self.assertEqual(observed[1].completion.step_id, observed[0].step_id)
        self.assertEqual(len(self.calls), 1)

    async def test_rejected_combined_commit_never_calls_model_or_publishes_start(self):
        groups = []
        record, commit = RunManager._record_engine_event, RecordTransaction.commit

        def capture(manager, project, run, store, event):
            if event.has_completion_start:
                groups.append(current_transaction())
            return record(manager, project, run, store, event)

        def reject(transaction):
            if transaction in groups:
                raise OSError('injected combined commit failure')
            return commit(transaction)

        with patch.object(RunManager, '_record_engine_event', capture), patch.object(RecordTransaction, 'commit', reject):
            run = await self.execute()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(self.calls, [])
        self.assertFalse(any(e.type in (EngineEventType.STEP_STARTED, EngineEventType.COMPLETION) for e in self.events))

    def test_combined_start_rejects_wrong_owner_and_finished_completion(self):
        pending = CompletionResult(step_id='model-step')
        for value in (replace(pending, step_id='elsewhere'), replace(pending, status='completed'),
                      replace(pending, ended_at=pending.started_at), replace(pending, usage_complete=True)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'pending Completion'):
                EngineEvent(EngineEventType.STEP_STARTED, step_id='model-step', completion=value)

    async def test_configured_usage_counter_keeps_durable_step_before_callback(self):
        from llm.services.composition import BackendServices
        starts, counted = [], []
        original = RunManager._record_engine_event

        def capture(manager, project, run, store, event):
            if event.type == EngineEventType.STEP_STARTED:
                starts.append((event, current_transaction().manager.records))
            return original(manager, project, run, store, event)

        def counter(request):
            self.assertEqual(len(starts), 1)
            self.assertFalse(starts[0][0].has_completion_start)
            self.assertEqual(starts[0][1].prepared, [])
            counted.append(True)
            return 1

        with tempfile.TemporaryDirectory() as folder:
            async with LargeLanguageModel(Path(folder), engines={'loop': self.app.engines.get('loop')},
                    services=BackendServices(token_counters={'probe': counter})) as backend:
                project = await backend.projects.acreate(config={'policies': {
                    'usage': {'max_tokens': 100, 'counter': 'probe'}}})
                await project.agents.acreate({'engine': 'loop',
                    'completion': {'model': 'test/model', 'max_tokens': 10}}, identifier='budgeted')
                session = await project.sessions.acreate(agent_ids=['budgeted'])
                with patch.object(RunManager, '_record_engine_event', capture):
                    request = await session.run.submit('count', recipients=['budgeted'])
                    run, = await request.wait(timeout=15)
                    await session.run.wait_idle()
                self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(counted, [True])

    async def test_external_selector_receives_durable_initial_preparation(self):
        journals, checked = [], []
        boundary = RunManager._steering_boundary
        outer = self

        class Selector:
            revision = 'test:durable-selector'
            async def select_tools(self, request):
                outer.assertTrue(journals)
                outer.assertEqual(journals[-1].prepared, [])
                checked.append(True)
                return request.available

        def prepared(manager, *args):
            journals.append(current_transaction().manager.records)
            return boundary(manager, *args)

        engine = self.app.engines.get('loop')
        with patch.object(engine, 'tool_selector', Selector()), patch.object(RunManager, '_steering_boundary', prepared):
            run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(checked, [True])

    def test_execution_boundary_is_a_closed_control_event(self):
        EngineEvent(EngineEventType.EXECUTION_BOUNDARY)
        for fields in ({'metadata': {'allow': True}}, {'step_id': 'x'}, {'name': 'work'}, {'error': 'x'}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, 'cannot carry'):
                EngineEvent(EngineEventType.EXECUTION_BOUNDARY, **fields)

    async def test_initial_checkpoint_channel_and_selection_prepare_before_execution_fence(self):
        prepared, committed, syncs = [], [], []
        original = RunManager._steering_boundary
        commit, sync = RecordTransaction.commit, os.fsync

        def boundary(manager, run, store, event, usage):
            if event.has_checkpoint:
                prepared.append(current_transaction())
                self.assertFalse(CheckpointDeclarations.read(run.metadata).names)
            result = original(manager, run, store, event, usage)
            if event.has_checkpoint:
                directive = SteeringEventData(event.metadata)
                self.assertTrue(directive.selects)
                self.assertIsNotNone(usage)
                saved = manager.repository.checkpoint(run, directive.checkpoint)
                self.assertIn(directive.boundary, CheckpointRecords.from_checkpoint(saved).records)
                self.assertEqual(self.calls, [])
            return result

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
        self.assertEqual(sum(bool(RecordJournal.BUNDLE.fullmatch(p)) for p in syncs), 0)
        self.assertTrue(await run.acheckpoint())
        self.assertTrue(any(e.type == EngineEventType.CHECKPOINT for e in self.events))

    async def test_pending_instruction_is_selected_by_atomic_open(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = RunManager._consume

        async def consume(manager, *args):
            entered.set()
            await release.wait()
            return await original(manager, *args)

        with patch.object(RunManager, '_consume', consume):
            request = await self.session.run.submit('original', recipients=['agent'])
            try:
                await asyncio.wait_for(entered.wait(), 15)
                run, = await request.aget_runs()
                instruction = await self.session.run.steer(run.id, 'before open')
            finally:
                release.set()
            run, = await request.wait(timeout=15)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([m['content'] for m in self.calls[0]['messages']].count('before open'), 1)
        checkpoint = await run.acheckpoint('loop')
        self.assertEqual(checkpoint['records']['steering:1']['message_ids'], [instruction.id])
        self.assertEqual((await run.ainstructions())[0].status, 'applied')

    async def test_instruction_after_first_selection_waits_for_next_boundary(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = CompletionPipeline.__aenter__

        async def prepare(pipeline):
            value = await original(pipeline)
            entered.set()
            await release.wait()
            return value

        with patch.object(CompletionPipeline, '__aenter__', prepare):
            request = await self.session.run.submit('original', recipients=['agent'])
            try:
                await asyncio.wait_for(entered.wait(), 15)
                run, = await request.aget_runs()
                instruction = await self.session.run.steer(run.id, 'during preparation')
            finally:
                release.set()
            run, = await request.wait(timeout=15)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn('during preparation', [m['content'] for m in self.calls[0]['messages']])
        self.assertEqual([m['content'] for m in self.calls[1]['messages']].count('during preparation'), 1)
        checkpoint = await run.acheckpoint('loop')
        self.assertEqual(checkpoint['records']['steering:1']['message_ids'], [])
        self.assertEqual(checkpoint['records']['steering:2']['message_ids'], [instruction.id])

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
            if not failed and prepared and RecordJournal.BUNDLE.fullmatch(name):
                failed.append(name)
                raise OSError('uncertain group fsync')
            return sync(fd)

        with patch.object(RunManager, '_steering_boundary', boundary), patch.object(os, 'fsync', fsync):
            run = await self.execute()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.calls, [])
        # The prepared checkpoint may be observed, but execution is not allowed
        # or announced when the following durable Step fence fails.
        self.assertFalse(any(e.type == EngineEventType.STEP_STARTED for e in self.events))
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

        def terminal(manager, *args, **kwargs):
            selected['finish'] = current_transaction()
            return finish(manager, *args, **kwargs)

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
