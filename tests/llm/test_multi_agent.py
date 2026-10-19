"""Recipient cardinality, scheduling, and durable ownership invariants."""

import asyncio
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from llm.llm import LargeLanguageModel
from llm.engines.base import BaseEngine, EngineEventType
from tests.llm.support.fake_engine import FakeStreamingEngine


class ParticipantTests(unittest.IsolatedAsyncioTestCase):
    async def test_membership_is_project_owned_and_survives_runtime_detail_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            entered, release = asyncio.Event(), asyncio.Event()
            async def observe(run, event):
                if event.type == EngineEventType.TEXT_DELTA:
                    entered.set()
            engine = FakeStreamingEngine(gate=release)
            async with LargeLanguageModel(Path(directory), engines={'echo': engine}, on_event=observe) as app:
                project = await app.projects.acreate()
                session = await project.sessions.acreate()
                await project.agents.acreate({'engine': 'echo'}, identifier='writer')
                await project.agents.acreate({'engine': 'echo'}, identifier='reviewer')
                self.assertEqual(await session.agents.aattach('writer'), ['writer'])
                self.assertEqual(await session.agents.aattach('writer'), ['writer'])
                foreign = await app.projects.acreate()
                await foreign.agents.acreate({'engine': 'echo'}, identifier='foreign')
                for invalid in ('missing', 'foreign', '../writer'):
                    with self.subTest(invalid=invalid), self.assertRaises((ValueError, FileNotFoundError)):
                        await session.agents.aattach(invalid)
                with self.assertRaisesRegex(ValueError, 'attached'):
                    await project.agents.adelete('writer')
                request = await session.run.submit('held', agent_id='writer')
                await asyncio.wait_for(entered.wait(), 10)
                try:
                    await session.agents.aattach('reviewer')
                    await session.agents.adetach('writer')
                    await session.asave(title='renamed', metadata={'preserve': True})
                finally:
                    release.set()
                self.assertEqual((await request.wait(timeout=10)).data.status, 'completed')
                data = await session.aget_data()
                self.assertEqual(data.agent_ids, ['reviewer'])
                self.assertEqual(data.title, 'renamed')
                self.assertTrue(data.metadata['preserve'])
                self.assertEqual(data.storage_version, 2)
                self.assertFalse(data.active_run_ids)
                await session.run.shutdown()
                reopened = await project.sessions.aload(session.id)
                self.assertEqual(await reopened.agents.alist(), ['reviewer'])

    async def test_actual_stage_c_session_read_is_non_migrating_then_attach_is_explicit(self):
        fixture = Path(__file__).parent / 'fixtures' / 'agent_run_v2'
        manifest = json.loads((fixture / 'manifest.json').read_text())
        original_hash = hashlib.sha256((fixture / 'workspace.tar.gz').read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with tarfile.open(fixture / 'workspace.tar.gz') as archive:
                archive.extractall(root, filter='data')
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest, name)
            async with LargeLanguageModel(root, engines={'echo': FakeStreamingEngine()}) as app:
                project = await app.projects.aload(manifest['project_id'])
                session = await project.sessions.aload(manifest['completed_session_id'])
                path = session.paths.root / 'session.json'
                before = path.read_bytes()
                self.assertEqual((await session.aget_data()).storage_version, 1)
                self.assertEqual(await session.agents.alist(), [])
                self.assertEqual(path.read_bytes(), before)
                await session.agents.aattach('writer')
                record = json.loads(path.read_text())
                self.assertEqual(record['storage_version'], 2)
                self.assertEqual(record['agent_ids'], ['writer'])
                self.assertEqual(record['current_run_ids'], [])
                done = await session.run.aload(manifest['completed_run_id'])
                self.assertEqual(done.data.agent_id, 'writer')
                self.assertEqual(done.data.status, 'completed')
        self.assertEqual(hashlib.sha256((fixture / 'workspace.tar.gz').read_bytes()).hexdigest(), original_hash)

    async def test_invalid_session_versions_and_identifier_sets_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(Path(directory), engines={'echo': FakeStreamingEngine()}) as app:
                project = await app.projects.acreate()
                session = await project.sessions.acreate()
                path = session.paths.root / 'session.json'
                original = json.loads(path.read_text())
                for changes in ({'storage_version': 3}, {'agent_ids': ['same', 'same']},
                                {'agent_ids': ['z', 'a']}, {'current_run_ids': ['../escape']},
                                {'storage_version': 1}):
                    value = {**original, **changes}
                    path.write_text(json.dumps(value))
                    before = path.read_bytes()
                    with self.subTest(changes=changes), self.assertRaises(ValueError):
                        await session.aget_data()
                    self.assertEqual(path.read_bytes(), before)
                path.write_text(json.dumps(original))


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_engines_really_overlap_and_complete_independently(self):
        entered = {name: asyncio.Event() for name in ('alpha', 'beta')}
        release = {name: asyncio.Event() for name in entered}
        completed = {name: asyncio.Event() for name in entered}
        contexts = {}
        class Probe(BaseEngine):
            async def execute(self, context):
                agent = context.run.agent_id
                contexts[agent] = context
                entered[agent].set()
                await release[agent].wait()
                async for event in super().execute(context):
                    yield event
            async def run(self, context):
                yield context.run.agent_id
        def observe(event):
            if event.run.status == 'completed':
                completed[event.run.agent_id].set()
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(Path(directory), engines={'probe': Probe()}, on_run_event=observe) as app:
                project = await app.projects.acreate()
                session = await project.sessions.acreate()
                for name in entered:
                    await project.agents.acreate({'engine': 'probe'}, identifier=name)
                    await session.agents.aattach(name)
                request = await session.run.submit('same accepted input', agent_ids=('beta', 'alpha'))
                try:
                    # Neither gate opens until BOTH execute() bodies entered.
                    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 10)
                    runs = await request.aruns()
                    self.assertEqual(len(runs), 2)
                    self.assertEqual({run.data.status for run in runs}, {'running'})
                    self.assertEqual((await session.aget_data()).active_run_ids, {run.id for run in runs})
                    self.assertIsNone((await session.aget_data()).current_run_id)
                    self.assertIsNone(request.data.run_id)
                    self.assertEqual(request.data.recipients, ('alpha', 'beta'))
                    self.assertEqual(contexts['alpha'].messages, contexts['beta'].messages)
                    self.assertTrue(all(run.data.input_message_id == request.id for run in runs))
                    self.assertEqual(len({run.data.assistant_message_id for run in runs}), 2)
                    with self.assertRaisesRegex(ValueError, 'Multiple recipients'):
                        await request.wait(timeout=1)
                    release['alpha'].set()
                    await asyncio.wait_for(completed['alpha'].wait(), 10)
                    beta = next(run for run in runs if run.data.agent_id == 'beta')
                    self.assertEqual((await session.aget_data()).active_run_ids, {beta.id})
                    self.assertEqual((await session.aget_data()).status, 'running')
                finally:
                    for event in release.values():
                        event.set()
                done = await request.wait_all(timeout=10)
                self.assertEqual([run.data.agent_id for run in done], ['alpha', 'beta'])
                for run in done:
                    self.assertEqual(run.data.status, 'completed', run.data.error)
                    self.assertEqual((await run.aresponse()).content, run.data.agent_id)
                    self.assertEqual((await run.aresponse()).run_id, run.id)
                    self.assertTrue(all(step.run_id == run.id for step in await run.steps.alist()))
                self.assertFalse((await session.aget_data()).active_run_ids)
                self.assertEqual((await session.aget_data()).status, 'idle')

    async def test_same_agent_fifo_does_not_block_a_different_agent(self):
        entered = {key: asyncio.Event() for key in ('first', 'second', 'other')}
        release = {key: asyncio.Event() for key in entered}
        order = []
        class Probe(BaseEngine):
            async def run(self, context):
                key = next(m.content for m in context.messages if m.id == context.run.input_message_id)
                order.append(key)
                entered[key].set()
                await release[key].wait()
                yield key
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(Path(directory), engines={'probe': Probe()}) as app:
                project = await app.projects.acreate()
                session = await project.sessions.acreate()
                for name in ('alpha', 'beta'):
                    await project.agents.acreate({'engine': 'probe'}, identifier=name)
                    await session.agents.aattach(name)
                first = await session.run.submit('first', agent_id='alpha')
                await asyncio.wait_for(entered['first'].wait(), 10)
                second = await session.run.submit('second', agent_id='alpha')
                other = await session.run.submit('other', agent_id='beta')
                try:
                    await asyncio.wait_for(entered['other'].wait(), 10)
                    self.assertFalse(entered['second'].is_set())
                    self.assertEqual(order, ['first', 'other'])
                    release['first'].set()
                    await first.wait(timeout=10)
                    await asyncio.wait_for(entered['second'].wait(), 10)
                    self.assertEqual(order, ['first', 'other', 'second'])
                finally:
                    for event in release.values():
                        event.set()
                for request in (second, other):
                    self.assertEqual((await request.wait(timeout=10)).data.status, 'completed')

    async def test_invalid_recipient_sets_have_no_durable_admission(self):
        engine = FakeStreamingEngine()
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(Path(directory), engines={'echo': engine}) as app:
                project = await app.projects.acreate()
                session = await project.sessions.acreate()
                await project.agents.acreate({'engine': 'echo'}, identifier='attached')
                await project.agents.acreate({'engine': 'echo'}, identifier='unattached')
                with self.assertRaises(ValueError):
                    await session.run.submit('empty all', recipients='all')
                await session.agents.aattach('attached')
                cases = ({'agent_ids': []}, {'agent_ids': ['attached', 'attached']},
                         {'agent_ids': ['attached', 'missing']}, {'agent_id': 'unattached'},
                         {'agent_ids': 'attached'}, {'recipients': None},
                         {'agent_id': 'attached', 'recipients': 'all'},
                         {'agent_ids': ['attached'], 'engine': 'echo'})
                for options in cases:
                    with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                        await session.run.submit('invalid', **options)
                self.assertEqual(await session.aconversation(), [])
                self.assertEqual(await session.run.alist(), [])
                self.assertEqual(engine.contexts, [])

class LegacyQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_c_queued_request_recovers_once_without_replaying_interrupted_run(self):
        fixture = Path(__file__).parent / 'fixtures' / 'agent_run_v2'
        manifest = json.loads((fixture / 'manifest.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with tarfile.open(fixture / 'workspace.tar.gz') as archive:
                archive.extractall(root, filter='data')
            engine = FakeStreamingEngine(chunks=('recovered',))
            async with LargeLanguageModel(root, engines={'echo': engine}) as app:
                project = await app.projects.aload(manifest['project_id'])
                session = await project.sessions.aload(manifest['queued_session_id'])
                await session.run.start()
                await asyncio.wait_for(session.run.wait_idle(), 10)
                self.assertEqual(len(engine.contexts), 1)
                self.assertEqual(engine.contexts[0].run.input_message_id, manifest['queued_message_id'])
                self.assertEqual(engine.contexts[0].run.agent_id, 'writer')
                rows = await session.run.alist()
                self.assertEqual(len(rows), 2)
                interrupted = await session.run.aload(manifest['interrupted_run_id'])
                self.assertEqual(interrupted.data.status, 'interrupted')
                await session.run.shutdown()
                await session.run.start()
                await asyncio.wait_for(session.run.wait_idle(), 10)
                self.assertEqual(len(engine.contexts), 1)
                self.assertFalse((await session.aget_data()).active_run_ids)
