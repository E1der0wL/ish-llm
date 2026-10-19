"""Independent Project Agents: ownership, old records, CAS, and crash boundaries."""

import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.core.agents import AgentDefinition
from llm.engines.base import BaseEngine
from llm.components.skills import SkillComponent
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from tests.llm.support.fake_engine import FakeStreamingEngine


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / 'fixtures/agent_v1'


class AgentDomainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = LargeLanguageModel(self.root / 'workspace', components=[],
                                      engines={'echo': FakeStreamingEngine(chunks=('answer',))})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate('independent')

    async def test_engine_only_crud_is_independent_and_revision_checked(self):
        agents = self.project.agents
        self.assertEqual(self.project.data.components, ())
        self.assertEqual(await agents.acreate({'engine': 'echo'}, identifier='one'), 'one')
        self.assertEqual(await agents.aload('one'), {'engine': 'echo'})
        self.assertEqual(agents.describe()['schema']['required'], ['engine'])
        with self.assertRaises(ValueError):
            self.project.components.agents
        snapshot = await agents.asnapshot('one')
        changed = await agents.arevise('one', {'engine': 'echo', 'description': 'new'},
                                      expected_revision=snapshot['revision'])
        self.assertNotEqual(changed['revision'], snapshot['revision'])
        with self.assertRaisesRegex(ValueError, 'Agent changed'):
            await agents.arevise('one', {'engine': 'echo'}, expected_revision=snapshot['revision'])
        with self.assertRaisesRegex(ValueError, 'revision is required'):
            await agents.arevise('one', {'engine': 'echo'}, expected_revision=None)
        with self.assertRaisesRegex(ValueError, 'Unregistered Agent engine'):
            await agents.acreate({'engine': 'missing'})
        with self.assertRaises(ValueError):
            await agents.acreate({})
        with self.assertRaises(ValueError):
            await agents.acreate({'engine': 'echo'}, identifier='../outside')
        with self.assertRaises(FileExistsError):
            await agents.acreate({'engine': 'echo'}, identifier='one')
        await agents.adelete('one', expected_version=changed['revision'])
        self.assertEqual(await agents.alist(), {})

    async def test_engine_capability_reads_domain_without_agent_component(self):
        class Reader(BaseEngine):
            required_capabilities = ('agents',)
            async def run(self, context):
                yield context.capabilities['agents'][0]['records']['one']['engine']
        self.app.engines.register('reader', Reader())
        await self.project.agents.acreate({'engine': 'echo'}, identifier='one')
        session = await self.project.sessions.acreate()
        run = await (await session.run.submit('read', engine='reader')).wait(timeout=10)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual((await run.aresponse()).content, 'echo')

    async def test_graph_uses_minimal_domain_agent_in_parent_run_step(self):
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.agent import AgentNode
        from tests.llm.test_agent_workflow import agent_graph
        class Worker(BaseEngine):
            def for_agent(self, definition):
                return self
            async def run(self, context):
                yield 'domain child'
        worker = Worker()
        self.app.engines.register('worker', worker)
        self.app.engines.register('graph', GraphEngine(handlers={'agent': AgentNode(engines={'worker': worker})}))
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.components.aselect(['workflows'])
        await self.project.agents.acreate({'engine': 'worker'}, identifier='writer')
        await self.project.components.workflows.acreate(agent_graph(), identifier='flow')
        session = await self.project.sessions.acreate()
        run = await (await session.run.submit('work', engine='graph', engine_options={'workflow': 'flow'})).wait(timeout=10)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(await session.run.alist()), 1)
        steps = [step for step in await run.steps.alist() if step.kind == 'agent']
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].run_id, run.id)
        self.assertEqual(steps[0].metadata['agent_id'], 'writer')
        self.assertEqual(steps[0].metadata['agent_revision'], (await self.project.agents.asnapshot('writer'))['revision'])
        await self.project.components.workflows.adelete('flow')
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.project.agents.adelete('writer')

    async def test_refinement_requires_approval_and_cas_without_agent_component(self):
        from llm.components.refinement import RefinementComponent
        self.app.project_manager.components.register(RefinementComponent())
        await self.project.components.aselect(['refinement'])
        await self.project.agents.acreate({'engine': 'echo'}, identifier='one')
        session = await self.project.sessions.acreate()
        run = await (await session.run.submit('evidence', engine='echo')).wait(timeout=10)
        self.assertEqual(run.data.status, 'completed')
        refine = self.project.components.refinement
        target = {'component': 'agents', 'identifier': 'one'}
        before = await refine.atarget_snapshot(target)
        identifier = await refine.acreate({'target': target, 'operation': 'update',
            'expected_version': before['version'], 'reason': 'document observed behavior',
            'evidence': [{'session_id': session.id, 'run_id': run.id}],
            'patch': {'description': 'reviewed'}})
        proposal = await refine.asnapshot(identifier)
        with self.assertRaisesRegex(ValueError, 'status'):
            await refine.aapply(identifier, expected_version=proposal['version'])
        self.assertEqual(await refine.atarget_snapshot(target), before)

        approved = await refine.aapprove(identifier, expected_version=proposal['version'])
        applied = await refine.aapply(identifier, expected_version=approved['version'])
        self.assertEqual((await self.project.agents.aload('one'))['description'], 'reviewed')
        await refine.arollback(identifier, expected_version=applied['version'])
        self.assertEqual(await refine.atarget_snapshot(target), before)

    async def test_running_definition_is_frozen_and_changed_paused_binding_is_rejected(self):
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.agent import AgentNode
        from tests.llm.test_agent_workflow import agent_graph
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Worker(BaseEngine):
            def __init__(self, description='first'):
                super().__init__()
                self.description = description
            def for_agent(self, definition):
                return Worker(definition.get('description', 'first'))
            async def run(self, context):
                calls.append(self.description)
                entered.set()
                await release.wait()
                yield self.description
        worker = Worker()
        self.app.engines.register('worker', worker)
        self.app.engines.register('graph', GraphEngine(handlers={'agent': AgentNode(engines={'worker': worker})}))
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.components.aselect(['workflows'])
        agents = self.project.agents
        await agents.acreate({'engine': 'worker'}, identifier='writer')
        before = await agents.asnapshot('writer')
        await self.project.components.workflows.acreate(agent_graph(), identifier='flow')
        session = await self.project.sessions.acreate()
        request = await session.run.submit('first', engine='graph', engine_options={'workflow': 'flow'})
        await asyncio.wait_for(entered.wait(), 10)
        try:
            await agents.arevise('writer', {'engine': 'worker', 'description': 'second'},
                                 expected_revision=before['revision'])
        finally:
            release.set()
        first = await request.wait(timeout=10)
        self.assertEqual(first.data.status, 'completed', first.data.error)
        step = next(step for step in await first.steps.alist() if step.kind == 'agent')
        self.assertEqual(step.metadata['agent_revision'], before['revision'])
        self.assertEqual(calls, ['first'])
        await self.project.components.workflows.acreate(agent_graph(pause_before=True), identifier='paused')
        paused = await (await session.run.submit('paused', engine='graph', engine_options={'workflow': 'paused'})).wait(timeout=10)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        selected = await agents.asnapshot('writer')
        await agents.arevise('writer', {'engine': 'worker', 'description': 'third'},
                             expected_revision=selected['revision'])
        with self.assertRaises(ValueError):
            await session.run.resume(paused.id, engine='graph')
        self.assertEqual(calls, ['first'])

    async def test_concurrent_cas_has_one_winner_and_one_conflict(self):
        agents = self.project.agents
        await agents.acreate({'engine': 'echo'}, identifier='one')
        version = (await agents.asnapshot('one'))['revision']
        ready, release = asyncio.Queue(), asyncio.Event()
        async def edit(label):
            ready.put_nowait(label)
            await release.wait()
            return await agents.arevise('one', {'engine': 'echo', 'description': label}, expected_revision=version)
        tasks = [asyncio.create_task(edit(label)) for label in ('a', 'b')]
        await ready.get()
        await ready.get()
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        snapshot = await agents.asnapshot('one')
        self.assertEqual(snapshot['revision'], next(r['revision'] for r in results if isinstance(r, dict)))

    async def test_inactive_records_clone_backup_restore_and_stale_handle(self):
        agents = self.project.agents
        await agents.acreate({'engine': 'echo'}, identifier='one')
        clone = await self.project.aclone()
        self.assertEqual(await clone.agents.asnapshot('one'), await agents.asnapshot('one'))
        self.assertEqual(clone.data.components, ())
        backup = await self.project.abackup(self.root / 'backup')
        async with LargeLanguageModel(self.root / 'restored', components=[], engines={'echo': FakeStreamingEngine()}) as other:
            restored = await other.projects.arestore_backup(backup)
            self.assertEqual(restored.id, self.project.id)
            self.assertEqual(await restored.agents.asnapshot('one'), await agents.asnapshot('one'))
        await self.project.adelete()
        with self.assertRaisesRegex(ValueError, 'deleted'):
            await agents.acreate({'engine': 'echo'})

    async def test_corrupt_envelope_and_linked_storage_are_not_overwritten(self):
        agents = self.project.agents
        await agents.acreate({'engine': 'echo'}, identifier='one')
        path = self.project.paths.agents / 'records/one.json'
        original = path.read_bytes()
        for changes in ({'storage_version': 999}, {'id': 'different'}, {'revision': 'bad'}):
            with self.subTest(changes=changes):
                value = json.loads(original)
                value.update(changes)
                path.write_text(json.dumps(value))
                corrupt = path.read_bytes()
                with self.assertRaises(ValueError):
                    await agents.asave('one', {'engine': 'echo'})
                self.assertEqual(path.read_bytes(), corrupt)
        path.write_bytes(original)
        path.unlink()
        external = self.root / 'external.json'
        external.write_bytes(original)
        path.symlink_to(external)
        with self.assertRaises(ValueError):
            await agents.aload('one')
        self.assertEqual(external.read_bytes(), original)

    async def test_failed_record_save_rolls_back_definition_and_revision(self):
        agents = self.project.agents
        await agents.acreate({'engine': 'echo'}, identifier='one')
        before = await agents.asnapshot('one')
        import llm.services.lifecycle.agents as domain
        real = domain.atomic_json
        reached = []
        def fail_after_write(path, value):
            real(path, value)
            reached.append(path)
            raise OSError('Agent write boundary')
        with patch.object(domain, 'atomic_json', side_effect=fail_after_write):
            with self.assertRaisesRegex(OSError, 'Agent write boundary'):
                await agents.arevise('one', {'engine': 'echo', 'description': 'not committed'},
                                     expected_revision=before['revision'])
        self.assertEqual(len(reached), 1)
        self.assertEqual(await agents.asnapshot('one'), before)

    async def test_domain_references_protect_skill_and_agent_without_activation(self):
        self.app.project_manager.components.register(SkillComponent())
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.components.aselect(['skills', 'workflows'])
        await self.project.components.skills.acreate({'instructions': 'review'}, identifier='review')
        skills = self.project.components.skills
        version = (await skills.asnapshot('review'))['version']
        await skills.awrite_file('review', 'reference.md', 'keep this evidence', expected_revision=version)
        await self.project.agents.acreate({'engine': 'echo', 'resources': {'skills': ['review']}}, identifier='one')
        impact = await self.project.components.skills.aimpact('review')
        self.assertEqual([row['agent_id'] for row in impact], ['one'])
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.project.components.skills.adelete('review')
        version = (await skills.asnapshot('review'))['version']
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await skills.adelete_file('review', 'reference.md', expected_revision=version)
        self.assertEqual(await skills.aread_file('review', 'reference.md', expected_revision=version), b'keep this evidence')
        graph = (WorkflowGraph(entry='work').node('work', 'agent', agent='one')
                 .node('done', 'end').connect('work', 'done').to_dict())
        await self.project.components.workflows.acreate(graph, identifier='flow')
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.project.agents.adelete('one')
        await self.project.components.workflows.adelete('flow')
        nested = (WorkflowGraph(entry='repeat', initial_state={'literal': {'type': 'agent', 'agent': 'literal'}})
                  .node('repeat', 'loop', body=graph, max_iterations=1, on_limit='continue')
                  .node('done', 'end').connect('repeat', 'done').to_dict())
        await self.project.agents.acreate({'engine': 'echo'}, identifier='literal')
        await self.project.components.workflows.acreate(nested, identifier='nested')
        await self.project.agents.adelete('literal')
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.project.agents.adelete('one')
        await self.project.components.workflows.adelete('nested')
        await self.project.agents.adelete('one')
        await self.project.components.skills.adelete('review')

    async def test_legacy_facade_shares_one_catalogue_and_disabling_does_not_delete(self):
        from llm.components.agents import AgentComponent
        self.app.project_manager.components.register(AgentComponent())
        await self.project.components.aselect(['agents'])
        legacy = self.project.components.agents
        with self.assertRaises(ValueError):
            await legacy.acreate({'engine': 'echo'}, identifier='invalid-legacy')
        await legacy.acreate({'purpose': 'legacy input contract', 'engine': 'echo'}, identifier='one')
        snapshot = await self.project.agents.asnapshot('one')
        await self.project.agents.arevise('one', {'engine': 'echo', 'description': 'shared'},
                                        expected_revision=snapshot['revision'])
        self.assertEqual(await legacy.aload('one'), await self.project.agents.aload('one'))
        with self.assertRaisesRegex(ValueError, 'Project.agents'):
            await self.project.components.aremove('agents', permanent=True)
        await self.project.components.aremove('agents')
        self.assertEqual((await self.project.agents.aload('one'))['description'], 'shared')
        clone = await self.project.aclone()
        self.assertEqual(await clone.agents.alist(), await self.project.agents.alist())


class LegacyAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pre_domain_records_keep_identity_and_queued_request(self):
        manifest = json.loads((FIXTURE / 'manifest.json').read_text())
        fixture_hash = hashlib.sha256((FIXTURE / 'workspace.tar.gz').read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(FIXTURE / 'workspace.tar.gz') as archive:
                archive.extractall(root, filter='data')
            for name, expected in manifest['files'].items():
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected, name)
            async with LargeLanguageModel(root, engines={'loop': FakeStreamingEngine(chunks=('resumed queue',))}) as app:
                project = await app.projects.aload(manifest['project_id'])
                path = project.paths.agents / 'records/legacy.json'
                old_bytes = path.read_bytes()
                self.assertEqual(await project.agents.asnapshot('legacy'), manifest['agent_snapshot'])
                self.assertEqual(path.read_bytes(), old_bytes)
                completed = await project.sessions.aload(manifest['completed_session_id'])
                run = await completed.run.aload(manifest['completed_run_id'])
                self.assertEqual(run.data.status, 'completed')
                queued = await project.sessions.aload(manifest['queued_session_id'])
                request = queued.run.request(manifest['queued_message_id'])
                await queued.run.start()
                result = await request.wait(timeout=10)
                self.assertEqual(result.data.status, 'completed', result.data.error)
                self.assertEqual(result.data.input_message_id, manifest['queued_message_id'])
                self.assertEqual((await result.aresponse()).content, 'resumed queue')
        self.assertEqual(hashlib.sha256((FIXTURE / 'workspace.tar.gz').read_bytes()).hexdigest(), fixture_hash)


class AgentCrashTests(unittest.TestCase):
    def test_before_write_after_write_and_after_commit_recover_from_disk(self):
        code = r'''
import os, sys
from pathlib import Path
from llm.llm import LargeLanguageModel
from llm.services.lifecycle import agents as domain
from llm.services.infrastructure.transactions import TransactionManager
from tests.llm.support.fake_engine import FakeStreamingEngine
root, project_id, point, marker = sys.argv[1:]
app = LargeLanguageModel(root, components=[], engines={'echo': FakeStreamingEngine()})
agents = app.projects.load(project_id).agents
before = agents.snapshot('one')
write, retire = domain.atomic_json, TransactionManager._retire
reached = []
def crash():
    Path(marker).write_text(point)
    os._exit({'before': 81, 'after': 82, 'committed': 83}[point])
def record(path, value):
    reached.append(str(path))
    if point == 'before': crash()
    write(path, value)
    if point == 'after': crash()
def cleanup(self, path):
    if point == 'committed' and reached and (path / 'COMMITTED').is_file(): crash()
    return retire(self, path)
domain.atomic_json = record
TransactionManager._retire = cleanup
agents.revise('one', {'engine': 'echo', 'description': 'committed value'}, expected_revision=before['revision'])
raise AssertionError('failpoint was not reached')
'''
        for point, expected_exit in [('before', 81), ('after', 82), ('committed', 83)]:
            with self.subTest(failpoint=point), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'workspace'
                app = LargeLanguageModel(root, components=[], engines={'echo': FakeStreamingEngine()})
                project = app.projects.create()
                project.agents.create({'engine': 'echo'}, identifier='one')
                before = project.agents.snapshot('one')
                identifier = project.id
                asyncio.run(app.shutdown())
                marker = Path(tmp) / 'reached'
                result = subprocess.run([sys.executable, '-c', code, str(root), identifier, point, str(marker)],
                                        cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
                self.assertEqual(marker.read_text(), point)
                fresh = LargeLanguageModel(root, components=[], engines={'echo': FakeStreamingEngine()})
                try:
                    snapshot = fresh.projects.load(identifier).agents.snapshot('one')
                    expected = {'engine': 'echo', 'description': 'committed value'} if point == 'committed' else before['definition']
                    self.assertEqual(snapshot['definition'], expected)
                    self.assertEqual(snapshot['revision'], AgentDefinition.revision(expected))
                finally:
                    asyncio.run(fresh.shutdown())


if __name__ == '__main__':
    unittest.main()
