"""Explicit host exchange, durable idempotency and independent target authority."""
import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel, BackendServices
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.services.runtime.transfers import TransferRecords
from tests.llm.support.fake_engine import FakeStreamingEngine
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class CrossSessionRequests(unittest.IsolatedAsyncioTestCase):
    async def setup_app(self, *, engines=None, services=None, components=(), storage='file'):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.echo = FakeStreamingEngine(chunks=('answer',))
        self.app = LargeLanguageModel(self.root, engines=engines or {'echo': self.echo},
            services=services, components=list(components))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(conversation_storage=storage,
            components=[c.name for c in components])
        self.source, self.target = await self.project.sessions.acreate(), await self.project.sessions.acreate()
        return self.app, self.project, self.source, self.target

    async def attach(self, names=('a',), engine='echo', **definition):
        for name in names:
            await self.project.agents.acreate({'engine': engine, **definition}, identifier=name)
            await self.target.agents.aattach(name)

    async def test_immediate_multi_recipient_acceptance_idempotency_and_reopen(self):
        entered = {name: asyncio.Event() for name in ('a', 'b')}
        release, seen = asyncio.Event(), []
        source_entered = asyncio.Event()
        class Hold(BaseEngine):
            async def run(self, context):
                seen.append(context)
                if context.messages[-1].content == 'source private':
                    source_entered.set()
                else:
                    entered[context.run.agent_id].set()
                await release.wait()
                yield context.run.agent_id
        await self.setup_app(engines={'hold': Hold()})
        await self.attach(('a', 'b'), engine='hold')
        self.addCleanup(release.set)
        await self.source.agents.aattach('a')
        source_request = await self.source.run.submit('source private', agent_id='a')
        await asyncio.wait_for(source_entered.wait(), 10)
        source_run = (await source_request.aget_run()).id
        first, second = await asyncio.gather(*(
            self.source.exchange.send(self.target.id, 'public input', agent_ids=('b', 'a'), key='same', source_run_id=source_run)
            for _ in range(2)))
        self.assertEqual((await first.asnapshot())['message_id'], (await second.asnapshot())['message_id'])
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), 10)
        snapshot = await first.asnapshot()
        self.assertEqual(len(snapshot['runs']), 2)
        self.assertEqual({r['status'] for r in snapshot['runs']}, {'running'})
        self.assertEqual({r['input_message_id'] for r in snapshot['runs']}, {snapshot['message_id']})
        self.assertEqual(snapshot['recipients'], ['a', 'b'])
        self.assertTrue(all([m.content for m in c.messages] == ['public input'] for c in seen
                            if c.session.id == self.target.id))
        self.assertEqual((await self.source.aget_data()).active_run_ids, {source_run})
        with self.assertRaisesRegex(ValueError, 'cycle'):
            await self.target.exchange.send(self.source.id, 'back', agent_ids=('a',), key='back',
                source_run_id=snapshot['runs'][0]['run_id'])
        with self.assertRaisesRegex(ValueError, 'transfer_conflict'):
            await self.source.exchange.send(self.target.id, 'changed', agent_ids=('a', 'b'), key='same')
        with self.assertRaises(asyncio.TimeoutError):
            await first.wait(timeout=.01)
        self.assertEqual({r['status'] for r in (await first.asnapshot())['runs']}, {'running'})
        waiter = asyncio.create_task(first.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual((await self.target.aget_data()).active_run_ids,
                         {r['run_id'] for r in snapshot['runs']})
        release.set()
        await source_request.wait(timeout=10)
        result = await first.wait(timeout=10)
        self.assertEqual({r['result']['text'] for r in result['runs']}, {'a', 'b'})
        self.assertEqual(len([m for m in await self.target.aconversation() if m.role == 'user']), 1)
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, engines={}, components=[]) as fresh:
            p = await fresh.projects.aload(self.project.id)
            s = await p.sessions.aload(self.source.id)
            restored = await s.exchange.aget('same')
            self.assertEqual(await restored.asnapshot(), result)
            self.assertEqual(await restored.wait(timeout=5), result)

    async def test_owned_ids_recipients_actor_and_memory_reject_before_ack(self):
        await self.setup_app()
        await self.attach()
        other = await self.app.projects.acreate()
        foreign = await other.sessions.acreate()
        for target, ids, source_run in ((foreign.id, ('a',), None), ('../x', ('a',), None),
                (self.source.id, ('a',), None), (self.target.id, (), None),
                (self.target.id, ('a', 'a'), None), (self.target.id, ('missing',), None),
                (self.target.id, ('a',), 'f' * 32)):
            with self.subTest(target=target, ids=ids, source_run=source_run):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    await self.source.exchange.send(target, 'invalid', agent_ids=ids, key='bad', source_run_id=source_run)
        self.assertEqual(await self.target.aconversation(), [])
        self.assertEqual(self.echo.contexts, [])
        await self.app.shutdown()
        await self.setup_app(storage='memory')
        await self.attach()
        with self.assertRaisesRegex(ValueError, 'file conversation'):
            await self.source.exchange.send(self.target.id, 'invalid', agent_ids=('a',), key='memory')
        self.assertEqual(await self.target.aconversation(), [])

    async def test_authorization_is_action_specific_and_rechecked_before_execution(self):
        permitted, seen = {'send', 'query', 'inspect', 'execute', 'cancel'}, []
        def authorize(value):
            seen.append(value)
            return value['action'] in permitted
        entered, release, effects = asyncio.Event(), asyncio.Event(), []
        class Hold(BaseEngine):
            async def run(self, context):
                if context.messages[-1].content == 'occupy':
                    entered.set()
                    await release.wait()
                else:
                    effects.append(context.run.id)
                yield 'done'
        await self.setup_app(engines={'hold': Hold()}, services=BackendServices(session_authorizer=authorize))
        await self.attach(engine='hold')
        self.addCleanup(release.set)
        self.assertEqual((await self.source.exchange.ainspect(self.target.id))['agent_ids'], ['a'])
        with self.assertRaises(PermissionError):
            await self.source.exchange.ainspect(self.target.id, conversation_limit=1)
        original = await self.target.run.submit('occupy', agent_id='a')
        await asyncio.wait_for(entered.wait(), 10)
        transfer = await self.source.exchange.send(self.target.id, 'denied at dispatch', agent_ids=('a',), key='deny')
        permitted.remove('execute')
        release.set()
        await original.wait(timeout=10)
        result = await transfer.wait(timeout=10)
        self.assertEqual([r['status'] for r in result['runs']], ['failed'])
        self.assertEqual(result['runs'][0]['error_code'], 'transfer_denied')
        self.assertEqual(effects, [])
        self.assertIn('execute', [v['action'] for v in seen])
        permitted.remove('query')
        with self.assertRaises(PermissionError):
            await transfer.asnapshot()
        permitted.remove('send')
        before = len(await self.target.aconversation())
        with self.assertRaises(PermissionError):
            await self.source.exchange.send(self.target.id, 'never', agent_ids=('a',), key='no')
        self.assertEqual(len(await self.target.aconversation()), before)
        self.app.project_manager.sessions.transfers.authorize = lambda _: 1
        with self.assertRaisesRegex(TypeError, 'literal boolean'):
            await self.source.exchange.ainspect(self.target.id)

    async def test_frozen_recipient_pending_cancel_and_target_deletion_protection(self):
        entered, release, effects = asyncio.Event(), asyncio.Event(), []
        class Hold(BaseEngine):
            async def run(self, context):
                if context.messages[-1].content == 'occupy':
                    entered.set()
                    await release.wait()
                else:
                    effects.append(context.run.agent_id)
                yield 'done'
        await self.setup_app(engines={'hold': Hold()})
        await self.attach(engine='hold')
        self.addCleanup(release.set)
        first = await self.target.run.submit('occupy', agent_id='a')
        await asyncio.wait_for(entered.wait(), 10)
        transfer = await self.source.exchange.send(self.target.id, 'cancelled', agent_ids=('a',), key='cancel')
        self.assertTrue(await transfer.cancel())
        frozen = await self.source.exchange.send(self.target.id, 'frozen', agent_ids=('a',), key='frozen')
        await self.target.agents.adetach('a')
        # Existing keys reopen independently of current membership.
        same = await self.source.exchange.send(self.target.id, 'cancelled', agent_ids=('a',), key='cancel')
        self.assertEqual((await same.asnapshot())['id'], (await transfer.asnapshot())['id'])
        with self.assertRaises(ValueError):
            await self.source.exchange.send(self.target.id, 'new', agent_ids=('a',), key='new')
        release.set()
        await first.wait(timeout=10)
        self.assertEqual([r['status'] for r in (await frozen.wait(timeout=10))['runs']], ['completed'])
        await self.target.run.shutdown()
        for session in (self.source, self.target):
            with self.assertRaisesRegex(ValueError, 'transfer receipt'):
                await session.adelete(permanent=True)
        self.assertEqual(effects, ['a'])

    async def test_absolute_deadline_before_ack_queued_dispatch_and_active_timeout(self):
        entered, release, effects = asyncio.Event(), asyncio.Event(), []
        class Hold(BaseEngine):
            async def run(self, context):
                text = context.messages[-1].content
                if text == 'occupy':
                    entered.set()
                    await release.wait()
                elif text == 'active':
                    entered.set()
                    await asyncio.Event().wait()
                else:
                    effects.append(text)
                yield 'done'
        await self.setup_app(engines={'hold': Hold()})
        await self.attach(engine='hold')
        self.addCleanup(release.set)
        before = datetime.now(timezone.utc)
        with self.assertRaisesRegex(ValueError, 'deadline expired'):
            await self.source.exchange.send(self.target.id, 'expired', agent_ids=('a',), key='old',
                deadline=(before - timedelta(seconds=1)).isoformat())
        self.assertEqual(await self.target.aconversation(), [])
        occupy = await self.target.run.submit('occupy', agent_id='a')
        await asyncio.wait_for(entered.wait(), 10)
        deadline = (before + timedelta(hours=1)).isoformat()
        transfer = await self.source.exchange.send(self.target.id, 'queued', agent_ids=('a',), key='later', deadline=deadline)
        with patch('llm.services.runtime.transfers.clock', return_value=before + timedelta(hours=2)):
            release.set()
            await occupy.wait(timeout=10)
            result = await transfer.wait(timeout=10)
            self.assertEqual([r['status'] for r in result['runs']], ['failed'])
            self.assertEqual(result['runs'][0]['error_code'], 'transfer_expired')
            self.assertEqual(effects, [])
            same = await self.source.exchange.send(self.target.id, 'queued', agent_ids=('a',), key='later', deadline=deadline)
            self.assertEqual((await same.asnapshot())['id'], result['id'])
        entered.clear()
        active = await self.source.exchange.send(self.target.id, 'active', agent_ids=('a',), key='active',
            deadline=(datetime.now(timezone.utc) + timedelta(seconds=.5)).isoformat())
        await asyncio.wait_for(entered.wait(), 10)
        result = await active.wait(timeout=10)
        self.assertEqual([r['status'] for r in result['runs']], ['failed'])
        self.assertEqual(result['runs'][0]['error_code'], 'run_timeout')

    async def test_source_run_provenance_cycles_and_target_private_context(self):
        await self.setup_app()
        await self.attach()
        await self.source.agents.aattach('a')
        origin = await (await self.source.run.submit('source-private', agent_id='a')).wait(timeout=10)
        transfer = await self.source.exchange.send(self.target.id, 'explicit-public', agent_ids=('a',),
            key='to-b', source_run_id=origin.id)
        result = await transfer.wait(timeout=10)
        self.assertEqual(result['source']['run_id'], origin.id)
        self.assertEqual(result['source']['agent_id'], 'a')
        self.assertEqual(result['source']['message_id'], origin.data.input_message_id)
        context = self.echo.contexts[-1]
        self.assertEqual([m.content for m in context.messages], ['explicit-public'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            await self.target.exchange.send(self.source.id, 'cycle', agent_ids=('a',),
                key='back', source_run_id=result['runs'][0]['run_id'])
        third = await self.project.sessions.acreate()
        await third.agents.aattach('a')
        next_transfer = await self.target.exchange.send(third.id, 'to-c', agent_ids=('a',),
            key='to-c', source_run_id=result['runs'][0]['run_id'])
        third_result = await next_transfer.wait(timeout=10)
        with self.assertRaisesRegex(ValueError, 'cycle'):
            await third.exchange.send(self.source.id, 'long-cycle', agent_ids=('a',), key='cycle',
                source_run_id=third_result['runs'][0]['run_id'])

    async def test_target_approval_resume_keeps_origin_and_runs_effect_once(self):
        effects = []
        async def effect(arguments):
            effects.append(arguments)
            return 'saved'
        tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, effect,
            contract=ToolContract(approval_required=True)),)))
        model = ScriptedCompletion([chunk(calls=[call('{}', name='write')], finish='tool_calls')],
                                   [chunk('approved', finish='stop')])
        await self.setup_app(engines={'loop': LoopEngine(completion_fn=model)}, components=[tools])
        await self.project.components.tools.aenable('write')
        await self.attach(engine='loop', completion={'model': 'test/model'}, tools=['write'])
        transfer = await self.source.exchange.send(self.target.id, 'save', agent_ids=('a',), key='approval')
        paused = await transfer.wait(timeout=10)
        self.assertEqual(paused['runs'][0]['status'], 'paused')
        self.assertEqual(effects, [])
        run = await self.target.run.aload(paused['runs'][0]['run_id'])
        interaction, = await run.ainteractions(pending_only=True)
        await run.arespond(interaction.respond('approve'))
        await transfer.resume(run.id)
        result = await transfer.wait(timeout=10)
        self.assertEqual([r['status'] for r in result['runs']], ['paused', 'completed'])
        self.assertEqual(result['runs'][-1]['previous_run_id'], run.id)
        self.assertEqual(effects, [{}])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            await self.target.exchange.send(self.source.id, 'back after resume', agent_ids=('a',), key='back',
                source_run_id=result['runs'][-1]['run_id'])
        with self.assertRaises(ValueError):
            await transfer.resume(run.id)
        self.assertEqual(effects, [{}])

    async def test_lifecycle_clone_archive_integrity_and_retention(self):
        await self.setup_app()
        await self.attach()
        transfer = await self.source.exchange.send(self.target.id, 'keep', agent_ids=('a',), key='keep')
        result = await transfer.wait(timeout=10)
        await self.target.run.shutdown()
        with self.assertRaisesRegex(ValueError, 'transfer receipt'):
            await self.target.adelete_turn(result['message_id'])
        clone = await self.target.aclone()
        self.assertEqual(await clone.run.alist(), [])
        self.assertFalse(any('transfer' in m.metadata for m in await clone.aconversation()))
        self.assertTrue(any('cloned_transfer_origin' in m.metadata for m in await clone.aconversation()))
        await self.project.aconfigure_policies({'retention': {'unit': 'run', 'max_bytes': 1, 'keep_runs': 0}})
        plan = await self.project.aretention()
        self.assertEqual(plan.candidates, [])
        self.assertTrue(plan.protected)
        backup = await self.project.abackup(self.root / 'backup')
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, engines={'echo': self.echo}, components=[]) as fresh:
                p = await fresh.projects.arestore_backup(backup)
                s = await p.sessions.aload(self.source.id)
                restored = await s.exchange.aget('keep')
                self.assertEqual(await restored.asnapshot(), result)
        receipt = TransferRecords.path(self.source.data, result['id'])
        saved = receipt.read_bytes()
        receipt.unlink()
        with self.assertRaises(FileNotFoundError):
            await self.source.exchange.aget('keep')
        with self.assertRaisesRegex(ValueError, 'without its receipt'):
            await self.source.exchange.send(self.target.id, 'keep', agent_ids=('a',), key='keep')
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.project.abackup(self.root / 'bad-backup')
        plan = await self.project.arecovery()
        self.assertIn('transfer_integrity', [i.code for i in plan.issues])
        self.assertEqual(len(await self.target.run.alist()), 1)
        receipt.write_bytes(saved)
        self.assertEqual((await self.source.exchange.aget('keep')).snapshot(), result)

    async def test_selected_cancel_preserves_sibling_and_needs_only_cancel_grant(self):
        entered = {n: asyncio.Event() for n in ('a', 'b')}
        release = {n: asyncio.Event() for n in ('a', 'b')}
        effects, allowed = [], {'send', 'query', 'execute', 'cancel'}
        class Hold(BaseEngine):
            async def run(self, context):
                name = context.run.agent_id
                entered[name].set()
                await release[name].wait()
                effects.append(name)
                yield name
        await self.setup_app(engines={'hold': Hold()},
            services=BackendServices(session_authorizer=lambda v: v['action'] in allowed))
        await self.attach(('a', 'b'), engine='hold')
        for event in release.values():
            self.addCleanup(event.set)
        transfer = await self.source.exchange.send(self.target.id, 'both', agent_ids=('a', 'b'), key='both')
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), 10)
        rows = {r['agent_id']: r for r in (await transfer.asnapshot())['runs']}
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            await transfer.cancel(run_id='f' * 32)
        allowed.remove('query')
        self.assertTrue(await transfer.cancel(run_id=rows['a']['run_id']))
        self.assertEqual((await self.target.aget_data()).active_run_ids, {rows['b']['run_id']})
        release['b'].set()
        allowed.add('query')
        result = await transfer.wait(timeout=10)
        self.assertEqual(result['status'], 'partial_failure')
        self.assertEqual({r['agent_id']: r['status'] for r in result['runs']}, {'a': 'interrupted', 'b': 'completed'})
        self.assertEqual(effects, ['b'])

    async def test_expired_approval_cannot_extend_deadline_through_either_resume_api(self):
        effects = []
        async def effect(arguments):
            effects.append(arguments)
            return 'saved'
        tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, effect,
            contract=ToolContract(approval_required=True)),)))
        model = ScriptedCompletion([chunk(calls=[call('{}', name='write')], finish='tool_calls')])
        await self.setup_app(engines={'loop': LoopEngine(completion_fn=model)}, components=[tools])
        await self.project.components.tools.aenable('write')
        await self.attach(engine='loop', completion={'model': 'test/model'}, tools=['write'])
        now = datetime.now(timezone.utc)
        deadline = (now + timedelta(hours=1)).isoformat()
        transfer = await self.source.exchange.send(self.target.id, 'save', agent_ids=('a',), key='expired', deadline=deadline)
        result = await transfer.wait(timeout=10)
        run = await self.target.run.aload(result['runs'][0]['run_id'])
        self.assertEqual(run.data.status, 'paused')
        interaction, = await run.ainteractions(pending_only=True)
        await run.arespond(interaction.respond('approve'))
        with patch('llm.services.runtime.transfers.clock', return_value=now + timedelta(hours=2)):
            for operation in (lambda: transfer.resume(run.id), lambda: self.target.run.resume(run.id)):
                with self.assertRaisesRegex(ValueError, 'deadline expired'):
                    await operation()
            plan = await self.target.run.resume_plan(run.id)
            self.assertFalse(plan.can_resume)
            self.assertEqual((await transfer.asnapshot())['deadline'], deadline)
        self.assertEqual(effects, [])
        self.assertEqual(len(await self.target.run.alist()), 1)

    async def test_corrupt_run_cannot_drop_transport_policy_or_archive_origin(self):
        await self.setup_app()
        await self.attach()
        transfer = await self.source.exchange.send(self.target.id, 'saved', agent_ids=('a',), key='origin')
        result = await transfer.wait(timeout=10)
        await self.target.run.shutdown()
        run = await self.target.run.aload(result['runs'][0]['run_id'])
        path = run.data.paths.root / 'run.json'
        original = path.read_bytes()
        value = json.loads(original)
        del value['metadata']['transfer']
        path.write_text(json.dumps(value))
        for operation in (transfer.asnapshot, lambda: self.project.abackup(self.root / 'invalid')):
            with self.assertRaisesRegex(ValueError, 'transfer origin'):
                await operation()
        plan = await self.project.arecovery()
        self.assertIn('invalid_recipient_group', [i.code for i in plan.issues])
        self.assertEqual(len(self.echo.contexts), 1)
        path.write_bytes(original)
        self.assertEqual(await transfer.asnapshot(), result)

    async def test_depth_bound_and_structured_origin_cannot_be_overridden(self):
        await self.setup_app()
        await self.attach()
        for values in ({'key': ''}, {'key': 'x' * 257}, {'deadline': '2026-10-19T00:00:00'},
                       {'source_agent_id': 'forged'}, {'source_message_id': 'f' * 32}):
            with self.subTest(values=values), self.assertRaises((ValueError, TypeError)):
                await self.source.exchange.send(self.target.id, 'input', agent_ids=('a',),
                                                **{'key': 'invalid', **values})
        self.assertEqual(await self.target.aconversation(), [])
        source, source_run = self.source, None
        target = self.target
        evidence = []
        for index in range(15):
            if index:
                target = await self.project.sessions.acreate()
                await target.agents.aattach('a')
            transfer = await source.exchange.send(target.id, 'next', agent_ids=('a',), key=str(index),
                                                  source_run_id=source_run)
            result = await transfer.wait(timeout=10)
            self.assertEqual(result['status'], 'completed')
            source, source_run = target, result['runs'][0]['run_id']
            evidence.append(result['id'])
        overflow = await self.project.sessions.acreate()
        await overflow.agents.aattach('a')
        with self.assertRaisesRegex(ValueError, 'depth limit'):
            await source.exchange.send(overflow.id, 'too deep', agent_ids=('a',), key='overflow', source_run_id=source_run)
        self.assertEqual(await overflow.aconversation(), [])
        self.assertEqual(len(self.echo.contexts), 15)
        print('E_DEPTH=' + json.dumps(evidence))
