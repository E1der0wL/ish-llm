"""Checkpoint loss must not turn completed work into an unstarted node."""
import hashlib
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from llm.components.workflows import WorkflowGraph
from llm.engines.graph import GraphEngine
from llm.llm import LargeLanguageModel
from llm.services.infrastructure.storage import revision_token
from llm.services.runtime import checkpoints
from llm.services.runtime.checkpoints import CheckpointRepository
from tests.llm import test_graph_checkpoints as fixture

class CheckpointIntegrityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.GraphCheckpointTests.asyncSetUp
    setup_graph = fixture.GraphCheckpointTests.setup_graph
    run_graph = fixture.GraphCheckpointTests.run_graph

    async def test_lost_completed_record_never_repeats_effect(self):
        effect = self.root / 'effects.txt'
        async def work(node):
            with effect.open('a') as stream:
                stream.write(node.node_id + '\n')
            return {}
        graph = (WorkflowGraph(entry='first').node('first', 'work')
                 .node('second', 'work', pause_before=True).node('end', 'end')
                 .connect('first', 'second').connect('second', 'end').to_dict())
        await self.setup_graph(graph, work)
        run = await self.run_graph()
        project_id, session_id, run_id = self.project.id, self.session.id, run.id
        self.assertEqual(effect.read_text(), 'first\n')
        path = run.data.paths.state / 'checkpoints' / 'graph' / 'records' / (hashlib.sha256(b'["first"]').hexdigest() + '.json')
        self.assertTrue(path.exists())
        await self.app.shutdown()
        path.unlink()
        app = LargeLanguageModel(self.root, engines={'graph': self.engine})
        self.addAsyncCleanup(app.shutdown)
        try:
            session = await (await app.projects.aload(project_id)).sessions.aload(session_id)
            await (await session.run.resume(run_id, agent_id='graph')).wait(timeout=30)
        except (ValueError, OSError):
            pass
        self.assertEqual(effect.read_text(), 'first\n')
        self.assertFalse(path.exists())

    async def test_completion_manifest_failure_keeps_started_evidence(self):
        effect = self.root / 'effects.txt'
        async def work(node):
            with effect.open('a') as stream:
                stream.write(node.node_id + '\n')
            return {}
        await self.setup_graph(fixture.straight(), work)
        original = checkpoints.atomic_json
        completing = False
        def fail_manifest(path, value):
            nonlocal completing
            if value.get('key') == '["work"]' and value['value']['status'] == 'completed':
                completing = True
            if completing and path.name == 'checkpoint.json':
                raise OSError('manifest write failed')
            return original(path, value)
        with patch.object(checkpoints, 'atomic_json', fail_manifest):
            run = await self.run_graph()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual((await run.acheckpoint())['records']['["work"]']['status'], 'started')
        with self.assertRaisesRegex(ValueError, 'retry_nodes'):
            await self.session.run.resume(run.id, agent_id='graph')
        self.assertEqual(effect.read_text(), 'work\n')

    async def test_process_death_between_record_manifest_and_run_preserves_uncertainty(self):
        code = textwrap.dedent('''
            import asyncio, os, sys
            from pathlib import Path
            from llm.components.workflows import WorkflowGraph
            from llm.engines.graph import GraphEngine
            from llm.llm import LargeLanguageModel
            from llm.services.runtime import checkpoints
            root, boundary = Path(sys.argv[1]), sys.argv[2]
            original = checkpoints.atomic_json
            completing = False
            def crash(path, value):
                global completing
                original(path, value)
                if value.get('key') == '["work"]' and value['value']['status'] == 'completed':
                    completing = True
                    if boundary == 'record':
                        os._exit(77)
                if completing and path.name == 'checkpoint.json':
                    os._exit(77)
            checkpoints.atomic_json = crash
            async def work(node):
                with (root / 'effects.txt').open('a') as stream:
                    stream.write('effect\\n')
                return {}
            async def main():
                async with LargeLanguageModel(root, engines={'graph': GraphEngine(handlers={'work': work})}) as app:
                    project = await app.projects.acreate(components=['workflows'])
                    graph = WorkflowGraph(entry='work').node('work', 'work').node('end', 'end').connect('work', 'end').to_dict()
                    await (await project.components.aget('workflows')).acreate(graph, identifier='flow')
                    await project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'flow'}}, identifier='graph')
                    session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
                    await (await session.run.submit('effect', recipients=['graph'])).wait()
            asyncio.run(main())
''')
        for boundary in ('record', 'manifest'):
            with self.subTest(boundary=boundary):
                root = self.root / boundary
                child = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(root), boundary,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, error = await asyncio.wait_for(child.communicate(), 20)
                self.assertEqual(child.returncode, 77, error.decode(errors='replace'))
                effect = root / 'effects.txt'
                self.assertEqual(effect.read_text(), 'effect\n')
                async def work(node):
                    with effect.open('a') as stream:
                        stream.write('replayed\n')
                    return {}
                async with LargeLanguageModel(root, engines={'graph': GraphEngine(handlers={'work': work})}) as app:
                    project = (await app.projects.alist())[0]
                    session = (await project.sessions.alist())[0]
                    await session.run.start()
                    await session.run.wait_idle()
                    run = (await session.run.alist())[0]
                    self.assertEqual(run.data.status, 'interrupted')
                    self.assertEqual((await run.acheckpoint())['records']['["work"]']['status'], 'started')
                    with self.assertRaises(ValueError):
                        await session.run.resume(run.id, agent_id='graph')
                    self.assertEqual(effect.read_text(), 'effect\n')


class CheckpointStorageIntegrityTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = SimpleNamespace(id='run', engine='custom', metadata={},
            paths=SimpleNamespace(state=self.root / 'state'))
        self.repository = CheckpointRepository()
        self.event = SimpleNamespace(metadata={'name': 'custom', 'operation': 'initialize',
            'header': {}, 'records': {}}, interaction=None)
        self.repository.record(self.run, self.event)
        self.path = self.root / 'state/checkpoints/custom/checkpoint.json'

    def test_manifest_fields_types_and_empty_state_are_explicit(self):
        self.assertEqual(self.repository.load(self.run, 'custom')['records'], {})
        original = json.loads(self.path.read_bytes())
        changes = [{key: value for key, value in original.items() if key != field} for field in original]
        changes += [{**original, field: value} for field in ('header', 'records', 'record_revisions')
                    for value in (None, False, 0, '', [])]
        changes += [{**original, 'schema_version': value} for value in (True, 1.0, None)]
        changes += [{**original, 'records': {'key': []}}, {**original, 'extra': None}]
        for value in changes:
            with self.subTest(value=value):
                owner = deepcopy(self.run)
                # Exercise semantic shape independently of the outer revision guard.
                owner.metadata['checkpoint_revisions']['custom'] = revision_token(value)
                self.path.write_text(json.dumps(value))
                before = self.path.read_bytes()
                with self.assertRaises((ValueError, TypeError)):
                    self.repository.load(owner, 'custom')
                self.assertEqual(self.path.read_bytes(), before)

    def test_lost_root_cannot_be_initialized_again(self):
        shutil.rmtree(self.path.parent)
        with self.assertRaises(ValueError):
            self.repository.record(self.run, self.event)
        self.assertFalse(self.path.exists())

    def test_lost_declaration_or_revision_cannot_hide_existing_checkpoint(self):
        for field in ('checkpoints', 'checkpoint_revisions'):
            owner = deepcopy(self.run)
            del owner.metadata[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.repository.names(owner)

    def test_changed_record_and_undeclared_files_are_not_accepted(self):
        self.repository.record(self.run, SimpleNamespace(metadata={'name': 'custom', 'operation': 'record',
            'key': 'node', 'value': {'private': 'stored'}}, interaction=None))
        path = next((self.path.parent / 'records').iterdir())
        before = path.read_bytes()
        path.write_text(json.dumps({'key': 'node', 'value': {'private': 'changed'}}))
        damaged = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.repository.load(self.run, 'custom')
        self.assertEqual(path.read_bytes(), damaged)
        path.write_bytes(before)
        extra = path.with_name('unknown.json')
        extra.write_bytes(before)
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            self.repository.load(self.run, 'custom')
        self.assertEqual(extra.read_bytes(), before)
