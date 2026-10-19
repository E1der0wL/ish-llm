"""Controlled D schedules: record IDs, ownership, and durable terminal outcomes."""
import asyncio
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel, BackendServices
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.components.tools import Tool, ToolRegistry, ToolContract
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class MultiRunInterleavings(unittest.IsolatedAsyncioTestCase):
    async def setup_app(self, engines, *, config=None, components=(), storage='file', services=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        app = LargeLanguageModel(root, engines=engines, components=list(components), services=services)
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(config=config, components=[c.name for c in components], conversation_storage=storage)
        session = await project.sessions.acreate()
        return app, project, session

    async def attach(self, project, session, definitions):
        for name, definition in definitions.items():
            await project.agents.acreate(definition, identifier=name)
            await session.agents.aattach(name)

    async def test_twenty_controlled_mixed_schedules_preserve_run_step_and_active_ids(self):
        # Fixed schedules, no timing sleeps: each terminal boundary is observed
        # before releasing the next Engine. Four Sessions isolate dispatcher state.
        entered, release, actions, seen = {}, {}, {}, {}
        class Probe(BaseEngine):
            async def run(self, context):
                key = (context.session.id, context.run.agent_id)
                seen[key] = context
                entered[key].set()
                await release[key].wait()
                if actions[key] == 'failed':
                    raise ValueError('controlled failure')
                yield context.run.agent_id
        app, project, initial = await self.setup_app({'probe': Probe()})
        definitions = {name: {'engine': 'probe'} for name in ('a', 'b', 'c')}
        await self.attach(project, initial, definitions)
        sessions = [initial] + [await project.sessions.acreate() for _ in range(3)]
        for session in sessions[1:]:
            for name in definitions:
                await session.agents.aattach(name)
        orders = list(itertools.permutations(definitions))
        evidence = []
        for schedule in range(20):
            session = sessions[schedule % 4]
            states = ('completed', 'failed', 'interrupted') if schedule % 3 else ('completed',) * 3
            for name, state in zip(definitions, states):
                key = (session.id, name)
                entered[key], release[key], actions[key] = asyncio.Event(), asyncio.Event(), state
            request = await session.run.submit(f'schedule-{schedule}', recipients='all')
            try:
                await asyncio.wait_for(asyncio.gather(*(entered[(session.id, name)].wait() for name in definitions)), 10)
                runs = {run.data.agent_id: run for run in await request.aruns()}
                remaining = {r.id for r in runs.values()}
                self.assertEqual((await session.aget_data()).active_run_ids, remaining)
                self.assertEqual(len({json.dumps(seen[(session.id, n)].run.metadata['context_snapshot'], sort_keys=True) for n in definitions}), 1)
                order = orders[schedule % len(orders)]
                for name in order:
                    key, run = (session.id, name), runs[name]
                    if actions[key] == 'interrupted':
                        self.assertTrue(await session.run.cancel(run.id))
                    else:
                        release[key].set()
                        # wait_all cannot finish until all siblings finish. A
                        # terminal run event provides an exact per-Run boundary.
                        manager = app._manager(session._snapshot)
                        control = manager._active.executions.get(run.id)
                        if control is not None:
                            await asyncio.wait_for(control.finished.wait(), 10)
                    remaining.remove(run.id)
                    data = await run.aget_data()
                    self.assertEqual(data.status, actions[key], (schedule, name, data.error))
                    self.assertEqual(data.input_message_id, request.id)
                    self.assertEqual((await run.aresponse()).run_id, run.id)
                    steps = await run.steps.alist()
                    self.assertTrue(steps)
                    self.assertTrue(all(step.run_id == run.id for step in steps))
                    self.assertEqual((await session.aget_data()).active_run_ids, remaining)
                self.assertEqual(len(await request.wait_all(timeout=10)), 3)
                evidence.append({'schedule': schedule, 'order': order, 'request': request.id,
                    'session': session.id, 'runs': {name: {'id': r.id, 'status': r.data.status} for name, r in runs.items()}})
            finally:
                for name in definitions:
                    release[(session.id, name)].set()
        await app.shutdown()
        async with LargeLanguageModel(project.paths.root.parent.parent, engines={}, components=[]) as fresh:
            loaded = await fresh.projects.aload(project.id)
            for session in await loaded.sessions.alist():
                self.assertFalse((await session.aget_data()).active_run_ids)
                for run in await session.run.alist():
                    self.assertNotIn(run.data.status, ('pending', 'running'))
        print('D_SCHEDULE_EVIDENCE=' + json.dumps(evidence, sort_keys=True))

    async def test_all_is_frozen_pending_cancel_and_parallel_limit_changes(self):
        entered, release = asyncio.Event(), asyncio.Event()
        effects = []
        class Hold(BaseEngine):
            async def run(self, context):
                effects.append(context.run.agent_id)
                entered.set()
                await release.wait()
                yield context.run.agent_id
        app, project, session = await self.setup_app({'hold': Hold()}, config={'policies': {'run': {'max_parallel_runs': 1}}})
        await self.attach(project, session, {n: {'engine': 'hold'} for n in ('a', 'b')})
        request = await session.run.submit('frozen', recipients='all')
        await asyncio.wait_for(entered.wait(), 10)
        try:
            rows = {r.data.agent_id: r for r in await request.aruns()}
            self.assertEqual(rows['b'].data.status, 'pending')
            self.assertEqual((await session.run.astatus()).queued_request_ids, [request.id])
            await session.agents.adetach('b')
            await project.agents.acreate({'engine': 'hold'}, identifier='later')
            await session.agents.aattach('later')
            self.assertEqual(request.data.recipients, ('a', 'b'))
            with self.assertRaises(ValueError):
                await session.adelete(permanent=True)
            self.assertTrue(await session.run.cancel(rows['b'].id))
            self.assertFalse(rows['b'].data.metadata['execution_started'])
            self.assertEqual((await rows['b'].aresponse()).status, 'cancelled')
            self.assertEqual((await session.aget_data()).active_run_ids, {rows['a'].id})
            await project.aconfigure_policies({'run': {'max_parallel_runs': 2}})
        finally:
            release.set()
        done = await request.wait_all(timeout=10)
        self.assertEqual([r.data.status for r in done], ['completed', 'cancelled'])
        self.assertEqual(effects, ['a'])
        self.assertEqual((await session.run.astatus()).queued_count, 0)

    async def test_cancel_wins_between_dispatch_read_and_start_without_effect(self):
        entered, release, at_start, continue_start = (asyncio.Event() for _ in range(4))
        effects = []
        class Probe(BaseEngine):
            async def run(self, context):
                effects.append(context.run.agent_id)
                entered.set()
                await release.wait()
                yield 'done'
        app, project, session = await self.setup_app({'probe': Probe()})
        await self.attach(project, session, {n: {'engine': 'probe'} for n in ('a', 'b')})
        manager = app._manager(session._snapshot)
        original_io = manager._io.run
        async def barrier(function, *args, **kwargs):
            if getattr(function, '__name__', '') == '_start_run' and args[1].agent_id == 'b':
                at_start.set()
                await continue_start.wait()
            return await original_io(function, *args, **kwargs)
        with patch.object(manager._io, 'run', barrier):
            request = await session.run.submit('race', recipients='all')
            try:
                await asyncio.wait_for(at_start.wait(), 10)
                rows = {r.data.agent_id: r for r in await request.aruns()}
                self.assertTrue(await session.run.cancel(rows['b'].id))
            finally:
                continue_start.set()
                release.set()
            done = await request.wait_all(timeout=10)
        self.assertEqual([r.data.status for r in done], ['completed', 'cancelled'])
        self.assertEqual(effects, ['a'])
        self.assertFalse((await session.aget_data()).active_run_ids)

    async def test_approval_pause_and_resumption_do_not_block_another_agent(self):
        effects, entered, release = [], asyncio.Event(), asyncio.Event()
        async def write(arguments):
            effects.append(arguments)
            return 'saved'
        tool = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, write,
            contract=ToolContract(approval_required=True)),)))
        provider = ScriptedCompletion([chunk(calls=[call('{}', name='write')], finish='tool_calls')], [chunk('accepted', finish='stop')])
        class Hold(BaseEngine):
            async def run(self, context):
                entered.set()
                await release.wait()
                yield 'independent'
        app, project, session = await self.setup_app({'loop': LoopEngine(completion_fn=provider), 'hold': Hold()}, components=[tool])
        await project.components.tools.aenable('write')
        await self.attach(project, session, {'writer': {'engine': 'loop', 'completion': {'model': 'test/model'}, 'tools': ['write'], 'policy': {'max_tool_calls': 1}}, 'other': {'engine': 'hold'}})
        holding = await session.run.submit('hold', agent_id='other')
        await asyncio.wait_for(entered.wait(), 10)
        try:
            paused = await (await session.run.submit('save', agent_id='writer')).wait(timeout=10)
            self.assertEqual(paused.data.status, 'paused', paused.data.error)
            self.assertEqual(effects, [])
            self.assertEqual((await session.aget_data()).active_run_ids, {holding.run.id})
            interaction, = await paused.ainteractions(pending_only=True)
            await paused.arespond(interaction.respond('approve'))
            resumed = await (await session.run.resume(paused.id)).wait(timeout=10)
            self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
            self.assertEqual(effects, [{}])
            self.assertEqual((await session.aget_data()).active_run_ids, {holding.run.id})
            self.assertNotEqual(resumed.data.input_message_id, paused.data.input_message_id)
            self.assertEqual(resumed.data.metadata['resume']['run_id'], paused.id)
        finally:
            release.set()
        self.assertEqual((await holding.wait(timeout=10)).data.status, 'completed')

    async def test_shared_turn_clone_backup_delete_and_atomic_retention(self):
        class Echo(BaseEngine):
            async def run(self, context):
                yield context.run.agent_id
        app, project, session = await self.setup_app({'echo': Echo()})
        await self.attach(project, session, {n: {'engine': 'echo'} for n in ('a', 'b')})
        request = await session.run.submit('original', recipients='all')
        runs = await request.wait_all(timeout=10)
        await session.run.shutdown()
        turns = await session.aturns()
        self.assertEqual([[m.id for m in t] for t in turns], [[request.id, *(r.data.assistant_message_id for r in runs)]])
        clone = await session.aclone()
        self.assertEqual(await clone.agents.alist(), ['a', 'b'])
        self.assertEqual(len(await clone.aturns()), 1)
        self.assertEqual(len((await clone.aturns())[0]), 3)
        self.assertEqual(await clone.run.alist(), [])
        backup = await project.abackup(project.paths.root.parent.parent / 'backup')
        with tempfile.TemporaryDirectory() as destination:
            async with LargeLanguageModel(destination, engines={'echo': Echo()}, components=[]) as other:
                restored = await other.projects.arestore_backup(backup)
                copied = await restored.sessions.aload(session.id)
                self.assertEqual(len(await copied.run.alist()), 2)
                self.assertEqual((await copied.aturns())[0][0].recipients, ('a', 'b'))
        await project.aconfigure_policies({'retention': {'unit': 'run', 'max_bytes': 1, 'keep_runs': 1}})
        self.assertEqual((await project.aretention()).candidates, [])
        await project.aconfigure_policies({'retention': {'keep_runs': 0}})
        plan = await project.aretention()
        self.assertEqual({row['run_id'] for row in plan.candidates}, {r.id for r in runs})
        self.assertEqual(sum(request.id in row['message_ids'] for row in plan.candidates), 1)
        await project.aretention(apply=True, expected_version=plan.version)
        self.assertEqual(await session.aconversation(), [])
        self.assertEqual(await session.run.alist(), [])
        await clone.adelete_turn((await clone.aturns())[0][0].id)
        self.assertEqual(await clone.aconversation(), [])

    async def test_memory_conversation_has_same_fanout_contract(self):
        class Echo(BaseEngine):
            async def run(self, context):
                yield context.run.agent_id
        app, project, session = await self.setup_app({'echo': Echo()}, storage='memory')
        await self.attach(project, session, {n: {'engine': 'echo'} for n in ('a', 'b')})
        request = await session.run.submit('one', recipients='all')
        self.assertEqual(len(await request.wait_all(timeout=10)), 2)
        self.assertEqual(len((await session.aturns())[0]), 3)
        self.assertFalse(session.paths.conversation.exists())

    async def test_parallel_tool_claims_share_one_receipt_and_do_not_overwrite(self):
        from llm.services.runtime.tools import ToolRuntime
        entered, release = asyncio.Event(), asyncio.Event()
        effects = []
        async def write(arguments):
            effects.append(arguments)
            entered.set()
            await release.wait()
            return 'one effect'
        tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, write),)))
        models = {name: ScriptedCompletion([chunk(calls=[call('{}', name='write')], finish='tool_calls')],
                                           [chunk('done', finish='stop')]) for name in ('a', 'b')}
        app, project, session = await self.setup_app({n: LoopEngine(completion_fn=m) for n, m in models.items()}, components=[tools],
            services=BackendServices(tool_runtime=ToolRuntime(operation_key=lambda call: 'shared-resource')))
        await project.components.tools.aenable('write')
        await self.attach(project, session, {n: {'engine': n, 'completion': {'model': 'test/model'}, 'tools': ['write']} for n in models})
        first = await session.run.submit('write', agent_id='a')
        await asyncio.wait_for(entered.wait(), 10)
        try:
            second = await (await session.run.submit('write', agent_id='b')).wait(timeout=10)
            self.assertEqual(second.data.status, 'failed')
            self.assertEqual(effects, [{}])
            self.assertEqual(session.run.operation('shared-resource')['status'], 'started')
            self.assertEqual((await session.aget_data()).active_run_ids, {first.run.id})
        finally:
            release.set()
        original = await first.wait(timeout=10)
        self.assertEqual(original.data.status, 'completed', original.data.error)
        receipt = session.run.operation('shared-resource')
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['run_id'], original.id)
        self.assertEqual(receipt['result'], 'one effect')
        self.assertEqual(effects, [{}])
        self.assertTrue(any(step.metadata.get('operation_receipt', {}).get('key') == 'shared-resource' for step in await original.steps.alist()))

    async def test_parallel_quota_reservations_are_atomic_and_unknown_usage_stays_unknown(self):
        gates = {n: asyncio.Event() for n in ('a', 'b')}
        class BarrierLoop(LoopEngine):
            async def execute(self, context):
                gates[context.run.agent_id].set()
                await asyncio.gather(*(gate.wait() for gate in gates.values()))
                async for event in super().execute(context):
                    yield event
        models = {n: ScriptedCompletion([chunk('done', finish='stop')]) for n in gates}
        app, project, session = await self.setup_app({n: BarrierLoop(completion_fn=m) for n, m in models.items()},
            config={'policies': {'usage': {'project_max_calls': 1}}})
        await self.attach(project, session, {n: {'engine': n, 'completion': {'model': 'test/model'}, 'tools': []} for n in gates})
        request = await session.run.submit('parallel quota', recipients='all')
        runs = await request.wait_all(timeout=10)
        self.assertEqual(sorted(r.data.status for r in runs), ['completed', 'failed'])
        self.assertEqual(sum(len(m.requests) for m in models.values()), 1)
        entries = [entry for run in runs for entry in run.data.metadata.get('completions', [])]
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]['usage_complete'])
        self.assertIsNone(entries[0]['reserved_tokens'])
        self.assertNotEqual(entries[0].get('usage', {}).get('total_tokens'), 0)
        failed = next(r for r in runs if r.data.status == 'failed')
        self.assertEqual(failed.data.error_code, 'usage_limit')

    async def test_completed_recipient_releases_lane_while_sibling_keeps_running(self):
        entered = {name: asyncio.Event() for name in ('a', 'b', 'next')}
        release = {name: asyncio.Event() for name in entered}
        contexts = {}
        class Probe(BaseEngine):
            async def run(self, context):
                key = 'next' if context.messages[-1].content == 'next' else context.run.agent_id
                contexts[key] = context.messages
                entered[key].set()
                await release[key].wait()
                yield key
        app, project, session = await self.setup_app({'probe': Probe()})
        await self.attach(project, session, {n: {'engine': 'probe'} for n in ('a', 'b')})
        group = await session.run.submit('group', recipients='all')
        try:
            await asyncio.wait_for(asyncio.gather(entered['a'].wait(), entered['b'].wait()), 10)
            rows = {r.data.agent_id: r for r in await group.aruns()}
            next_request = await session.run.submit('next', agent_id='a')
            release['a'].set()
            await asyncio.wait_for(entered['next'].wait(), 10)
            self.assertEqual(rows['a'].data.status, 'completed')
            self.assertEqual(rows['b'].data.status, 'running')
            self.assertEqual(contexts['a'], contexts['b'])
            self.assertIn('a', [m.content for m in contexts['next']])
            self.assertNotIn('b', [m.content for m in contexts['next']])
        finally:
            for gate in release.values():
                gate.set()
        self.assertEqual((await next_request.wait(timeout=10)).data.status, 'completed')
        self.assertEqual([r.data.status for r in await group.wait_all(timeout=10)], ['completed', 'completed'])

    async def test_agent_timeout_preserves_other_active_run_and_tool_effect(self):
        entered, release, timed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        effects = []
        class Slow(BaseEngine):
            async def run(self, context):
                await entered.wait()
                timed.set()
                await asyncio.Event().wait()
                yield 'unreachable'
        async def write(arguments):
            entered.set()
            await release.wait()
            effects.append(arguments)
            return 'saved'
        tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, write),)))
        model = ScriptedCompletion([chunk(calls=[call('{}', name='write')], finish='tool_calls')], [chunk('done', finish='stop')])
        app, project, session = await self.setup_app({'slow': Slow(), 'loop': LoopEngine(completion_fn=model)}, components=[tools])
        await project.components.tools.aenable('write')
        await self.attach(project, session, {'slow': {'engine': 'slow', 'policy': {'timeout_seconds': .2}},
            'writer': {'engine': 'loop', 'completion': {'model': 'test/model'}, 'tools': ['write']}})
        writer = await session.run.submit('write', agent_id='writer')
        await asyncio.wait_for(entered.wait(), 10)
        try:
            failed = await (await session.run.submit('timeout', agent_id='slow')).wait(timeout=10)
            self.assertTrue(timed.is_set())
            self.assertEqual(failed.data.status, 'failed')
            self.assertEqual((await session.aget_data()).active_run_ids, {writer.run.id})
            self.assertEqual(effects, [])
            self.assertEqual(app.observability.snapshot()['runtime']['active_runs'], 1)
        finally:
            release.set()
        finished = await writer.wait(timeout=10)
        self.assertEqual(finished.data.status, 'completed', finished.data.error)
        self.assertEqual(effects, [{}])
        self.assertFalse((await session.aget_data()).active_run_ids)

    async def test_simultaneous_same_agent_submits_preserve_acceptance_fifo(self):
        entered, release = asyncio.Event(), asyncio.Event()
        order = []
        class Probe(BaseEngine):
            async def run(self, context):
                order.append(context.run.input_message_id)
                entered.set()
                await release.wait()
                yield context.messages[-1].content
        app, project, session = await self.setup_app({'probe': Probe()})
        await self.attach(project, session, {'a': {'engine': 'probe'}})
        requests = await asyncio.gather(session.run.submit('one', agent_id='a'), session.run.submit('two', agent_id='a'))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            self.assertEqual(len(order), 1)
            accepted = [m.id for m in await session.aconversation() if m.role == 'user']
            self.assertEqual(order, accepted[:1])
            self.assertEqual(app.observability.snapshot()['runtime']['queued_requests'], 1)
            self.assertEqual(app.observability.snapshot()['runtime']['active_runs'], 1)
        finally:
            release.set()
        runs = await asyncio.gather(*(request.wait(timeout=10) for request in requests))
        self.assertEqual(order, accepted)
        self.assertEqual(len({r.id for r in runs}), 2)
        self.assertEqual([r.data.input_message_id for r in runs], [r.id for r in requests])

    async def test_pending_fanout_backup_restores_only_proven_unstarted_recipient(self):
        entered = asyncio.Event()
        class Hold(BaseEngine):
            async def run(self, context):
                entered.set()
                await asyncio.Event().wait()
                yield 'unreachable'
        app, project, session = await self.setup_app({'probe': Hold()}, config={'policies': {'run': {'max_parallel_runs': 1}}})
        await self.attach(project, session, {n: {'engine': 'probe'} for n in ('a', 'b')})
        request = await session.run.submit('durable pending', recipients='all')
        await asyncio.wait_for(entered.wait(), 10)
        with self.assertRaises(ValueError):
            await session.aclone()
        with self.assertRaises(ValueError):
            await session.adelete_turn(request.id)
        await session.run.shutdown()
        self.assertEqual([r.data.status for r in await request.aruns()], ['interrupted', 'pending'])
        backup = await project.abackup(project.paths.root.parent.parent / 'pending-backup')
        from tests.llm.support.fake_engine import FakeStreamingEngine
        probe = FakeStreamingEngine(chunks=('recovered',))
        with tempfile.TemporaryDirectory() as destination:
            async with LargeLanguageModel(destination, engines={'probe': probe}, components=[]) as fresh:
                restored = await fresh.projects.arestore_backup(backup)
                copied = await restored.sessions.aload(session.id)
                await copied.run.start()
                await asyncio.wait_for(copied.run.wait_idle(), 10)
                self.assertEqual([c.run.agent_id for c in probe.contexts], ['b'])
                self.assertEqual({r.data.agent_id: r.data.status for r in await copied.run.alist()}, {'a': 'interrupted', 'b': 'completed'})
                self.assertFalse((await copied.aget_data()).active_run_ids)

    async def test_request_cancel_wins_before_group_transaction_without_stale_queue(self):
        class Unexpected(BaseEngine):
            async def run(self, context):
                raise AssertionError('Cancelled admission must not invoke an Engine')
                yield
        app, project, session = await self.setup_app({'probe': Unexpected()})
        await self.attach(project, session, {n: {'engine': 'probe'} for n in ('a', 'b')})
        manager = app._manager(session._snapshot)
        reached, release = asyncio.Event(), asyncio.Event()
        io = manager._io.run
        async def intercept(function, *args, **kwargs):
            if getattr(function, '__name__', '') == '_admit_group':
                reached.set()
                await release.wait()
            return await io(function, *args, **kwargs)
        with patch.object(manager._io, 'run', intercept):
            request = await session.run.submit('cancel admission', recipients='all')
            await asyncio.wait_for(reached.wait(), 10)
            try:
                self.assertTrue(await request.cancel())
            finally:
                release.set()
            await asyncio.wait_for(session.run.wait_idle(), 10)
        self.assertEqual(request.data.status, 'cancelled')
        self.assertEqual(await request.aruns(), [])
        self.assertEqual(app.observability.snapshot()['runtime']['queued_requests'], 0)
        self.assertEqual(manager._active.groups, {})
        self.assertFalse((await session.aget_data()).active_run_ids)


    async def test_selected_cancel_bridges_running_notification_before_consumer_exists(self):
        entered, hold, publishing, release, checked = (asyncio.Event() for _ in range(5))
        effects = []
        class Probe(BaseEngine):
            async def run(self, context):
                if context.run.agent_id == 'b':
                    entered.set()
                    await hold.wait()
                effects.append(context.run.agent_id)
                yield context.run.agent_id
        app, project, session = await self.setup_app({'probe': Probe()})
        await self.attach(project, session, {n: {'engine': 'probe'} for n in ('a', 'b')})
        other = await session.run.submit('other', agent_id='b')
        await asyncio.wait_for(entered.wait(), 10)
        manager = app._manager(session._snapshot)
        publish, io = manager._publish_run, manager._io.run
        async def observing(run):
            await publish(run)
            if run.agent_id == 'a' and run.status == 'running':
                publishing.set()
                await release.wait()
        async def storage(function, *args, **kwargs):
            result = await io(function, *args, **kwargs)
            if getattr(function, '__name__', '') == '_cancel_pending_run':
                checked.set()
            return result
        with patch.object(manager, '_publish_run', observing), patch.object(manager._io, 'run', storage):
            request = await session.run.submit('cancel before Engine', agent_id='a')
            await asyncio.wait_for(publishing.wait(), 10)
            try:
                run, = await request.aruns()
                cancelled = asyncio.create_task(session.run.cancel(run.id))
                await asyncio.wait_for(checked.wait(), 10)
                self.assertFalse(cancelled.done())
            finally:
                release.set()
            self.assertTrue(await asyncio.wait_for(cancelled, 10))
            self.assertEqual((await request.wait(timeout=10)).data.status, 'interrupted')
            self.assertEqual(effects, [])
            self.assertEqual((await session.aget_data()).active_run_ids, {other.run.id})
        hold.set()
        self.assertEqual((await other.wait(timeout=10)).data.status, 'completed')
        self.assertEqual(effects, ['b'])
