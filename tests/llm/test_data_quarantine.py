"""Owner-proven Data quarantine preserves damaged bytes and never reopens the package."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel, DataComponent
from llm.components.skills import SkillComponent
from tests.llm import test_data_component as data_tests


class DataQuarantineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = data_tests.DataTests.asyncSetUp

    async def damaged(self):
        before = await self.data.asnapshot('samples')
        await self.data.awrite_file('samples', 'payload.bin', b'original', expected_revision=before['version'])
        package = self.project.paths.root / 'data/samples'
        (package / 'files/payload.bin').write_bytes(b'damaged bytes')
        with self.assertRaises(ValueError):
            await self.data.aload('samples')
        return package

    async def test_quarantine_preserves_bytes_and_blocks_read_recreate_after_restart(self):
        package = await self.damaged()
        definition = (package / 'data.json').read_bytes()
        plan = await self.data.ainspect_quarantine('samples')
        saved = await self.data.aquarantine('samples', expected_revision=plan['version'])
        self.assertEqual((package / 'quarantine/files/payload.bin').read_bytes(), b'damaged bytes')
        self.assertEqual((package / 'data.json').read_bytes(), definition)
        self.assertFalse((package / 'files').exists())
        with self.assertRaises(ValueError):
            await self.data.aload('samples')
        with self.assertRaises(FileExistsError):
            await self.data.acreate({'title': 'replacement'}, identifier='samples')
        await self.app.shutdown()
        async with LargeLanguageModel(self.root / 'workspace', components=[DataComponent()]) as reopened:
            data = (await reopened.projects.aload(self.project.id)).components['data']
            self.assertEqual(await data.aquarantine_snapshot('samples'), saved)
            with self.assertRaises(ValueError):
                await data.aload('samples')
            await data.adelete_quarantined('samples', expected_revision=saved['version'])
            self.assertFalse(package.exists())

    async def test_stale_inspection_or_corrupt_definition_never_moves_source(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        source = package / 'files/payload.bin'
        source.write_bytes(b'changed after inspection')
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await self.data.aquarantine('samples', expected_revision=plan['version'])
        self.assertEqual(source.read_bytes(), b'changed after inspection')
        self.assertFalse((package / 'quarantine').exists())
        definition = package / 'data.json'
        definition.write_bytes(b'{broken definition')
        with self.assertRaises(ValueError):
            await self.data.ainspect_quarantine('samples')
        self.assertEqual(definition.read_bytes(), b'{broken definition')
        self.assertEqual(source.read_bytes(), b'changed after inspection')

    async def test_unregistered_only_bytes_do_not_prove_a_quarantine_block(self):
        package = self.project.paths.root / 'data/samples'
        files = package / 'files'
        files.mkdir()
        source = files / 'unknown'
        source.write_bytes(b'unregistered only')
        definition = (package / 'data.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'declared attachments'):
            await self.data.ainspect_quarantine('samples')
        self.assertEqual(source.read_bytes(), b'unregistered only')
        self.assertEqual((package / 'data.json').read_bytes(), definition)
        self.assertFalse((package / 'quarantine').exists())

    async def test_lost_quarantine_receipt_tree_never_reopens_declared_source(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        await self.data.aquarantine('samples', expected_revision=plan['version'])
        import shutil
        shutil.rmtree(package / 'quarantine')
        definition = (package / 'data.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            await self.data.aload('samples')
        with self.assertRaises(ValueError):
            await self.data.ainspect_quarantine('samples')
        with self.assertRaises(FileExistsError):
            await self.data.acreate({'title': 'replacement'}, identifier='samples')
        self.assertEqual((package / 'data.json').read_bytes(), definition)
        self.assertFalse((package / 'quarantine').exists())

    async def test_attached_runtime_blocks_quarantine_and_explicit_delete(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        session = await self.project.sessions.acreate()
        await session.run.start()
        with self.assertRaisesRegex(ValueError, 'attached'):
            await self.data.aquarantine('samples', expected_revision=plan['version'])
        self.assertFalse((package / 'quarantine').exists())
        await session.run.shutdown()
        saved = await self.data.aquarantine('samples', expected_revision=plan['version'])
        await session.run.start()
        with self.assertRaisesRegex(ValueError, 'attached'):
            await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
        self.assertEqual((package / 'quarantine/files/payload.bin').read_bytes(), b'damaged bytes')
        await session.run.shutdown()

    async def test_skill_reference_is_checked_again_before_each_mutation(self):
        skill_owner = SkillComponent()
        async with LargeLanguageModel(self.root / 'referenced', components=[DataComponent(), skill_owner]) as app:
            project = await app.projects.acreate(components=['data', 'skills'])
            data, skills = project.components['data'], project.components['skills']
            await data.acreate({'title': 'Manual'}, identifier='manual')
            before = await data.asnapshot('manual')
            written = await data.awrite_file('manual', 'source.txt', 'source', expected_revision=before['version'])
            reference = {'component': 'data', 'identifier': 'manual', 'path': 'source.txt', 'revision': written['version']}
            await skills.acreate({'instructions': 'Use source', 'resources': [reference]}, identifier='guide')
            await project.components.aremove('skills')
            package = project.paths.root / 'data/manual'
            (package / 'files/source.txt').write_bytes(b'damaged')
            plan = await data.ainspect_quarantine('manual')
            with self.assertRaisesRegex(ValueError, 'referenced'):
                await data.aquarantine('manual', expected_revision=plan['version'])
            self.assertEqual((package / 'files/source.txt').read_bytes(), b'damaged')
            await project.components.aselect(['data', 'skills'])
            await skills.adelete('guide')
            saved = await data.aquarantine('manual', expected_revision=plan['version'])
            with self.assertRaisesRegex(ValueError, 'quarantined'):
                await skills.acreate({'instructions': 'Use source', 'resources': [reference]}, identifier='guide')
            # Inject a persisted reference through its owner writer. Normal resource resolution
            # above correctly refuses it, but deletion must still check stored references.
            current = await project.aget_data()
            with app.project_manager.ownership.scope():
                skill_owner.create(current, {'instructions': 'Use source', 'resources': [reference]}, identifier='guide')
            with self.assertRaisesRegex(ValueError, 'referenced'):
                await data.adelete_quarantined('manual', expected_revision=saved['version'])
            self.assertEqual((package / 'quarantine/files/source.txt').read_bytes(), b'damaged')

    async def test_failed_source_move_rolls_back_copy_and_receipt(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        definition = (package / 'data.json').read_bytes()
        with patch('llm.components.data.component.remove_named_tree', side_effect=OSError('move failed')):
            with self.assertRaises(OSError):
                await self.data.aquarantine('samples', expected_revision=plan['version'])
        self.assertEqual((package / 'files/payload.bin').read_bytes(), b'damaged bytes')
        self.assertEqual((package / 'data.json').read_bytes(), definition)
        self.assertFalse((package / 'quarantine').exists())

    async def test_upper_removal_and_backup_cannot_bypass_quarantine_evidence(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        await self.data.aquarantine('samples', expected_revision=plan['version'])
        definition = (package / 'data.json').read_bytes()
        receipt = package / 'quarantine/receipt.json'
        original = receipt.read_bytes()
        destination = self.root / 'backup'
        for damage in (False, True):
            if damage:
                receipt.write_bytes(b'{broken receipt')
            before = receipt.read_bytes()
            for operation in (lambda: self.data.adelete('samples'),
                              lambda: self.project.components.aremove('data', permanent=True),
                              lambda: self.project.adelete(permanent=True),
                              lambda: self.project.abackup(destination), self.project.aclone):
                with self.subTest(damage=damage, operation=operation), self.assertRaises(ValueError):
                    await operation()
                self.assertEqual(receipt.read_bytes(), before)
                self.assertEqual((package / 'data.json').read_bytes(), definition)
                self.assertEqual((package / 'quarantine/files/payload.bin').read_bytes(), b'damaged bytes')
                self.assertFalse(destination.exists())
        receipt.write_bytes(original)

    async def test_unknown_session_evidence_refuses_quarantine_in_place(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        session = await self.project.sessions.acreate()
        record = session.paths.root / 'session.json'
        record.write_bytes(b'{lost execution state')
        with self.assertRaises(ValueError):
            await self.data.aquarantine('samples', expected_revision=plan['version'])
        self.assertEqual(record.read_bytes(), b'{lost execution state')
        self.assertEqual((package / 'files/payload.bin').read_bytes(), b'damaged bytes')
        self.assertFalse((package / 'quarantine').exists())

    async def test_missing_or_corrupt_receipt_cannot_authorize_delete(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        saved = await self.data.aquarantine('samples', expected_revision=plan['version'])
        path = package / 'quarantine/receipt.json'
        original = path.read_bytes()
        for key in saved['data']:
            value = json.loads(original)
            del value[key]
            path.write_text(json.dumps(value))
            damaged = path.read_bytes()
            with self.subTest(key=key), self.assertRaises((ValueError, OSError)):
                await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
            self.assertEqual(path.read_bytes(), damaged)
            self.assertEqual((package / 'quarantine/files/payload.bin').read_bytes(), b'damaged bytes')
        path.unlink()
        with self.assertRaises((ValueError, OSError)):
            await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
        self.assertTrue(package.exists())

    async def test_receipt_types_binding_and_preserved_bytes_are_required(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        saved = await self.data.aquarantine('samples', expected_revision=plan['version'])
        path = package / 'quarantine/receipt.json'
        original = path.read_bytes()
        mutations = {'schema_version': [True, 1.0, 2, None], 'status': ['ready', None],
                     'id': ['', None], 'project_id': ['another'], 'component': ['skills'],
                     'identifier': ['another'], 'record_revision': ['changed'],
                     'created_at': ['invalid', '2026-10-21T00:00:00'], 'files': [{}, None]}
        for key, values in mutations.items():
            for value in values:
                changed = {**saved['data'], key: value}
                path.write_text(json.dumps(changed))
                damaged = path.read_bytes()
                with self.subTest(key=key, value=value), self.assertRaises((ValueError, OSError)):
                    await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
                self.assertEqual(path.read_bytes(), damaged)
                self.assertEqual((package / 'quarantine/files/payload.bin').read_bytes(), b'damaged bytes')
        path.write_bytes(original)
        payload = package / 'quarantine/files/payload.bin'
        payload.write_bytes(b'changed after quarantine')
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
        self.assertEqual(payload.read_bytes(), b'changed after quarantine')
        payload.unlink()
        payload.parent.rmdir()
        with self.assertRaises(ValueError):
            await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
        self.assertEqual(path.read_bytes(), original)

    async def test_unknown_entries_links_and_nonregular_files_remain_in_place(self):
        package = await self.damaged()
        stray = package / 'unowned.bin'
        stray.write_bytes(b'unknown origin')
        with self.assertRaisesRegex(ValueError, 'package entries'):
            await self.data.ainspect_quarantine('samples')
        self.assertEqual(stray.read_bytes(), b'unknown origin')
        stray.unlink()
        outside = self.root / 'outside'
        outside.write_bytes(b'outside bytes')
        entry = package / 'files/linked'
        entry.symlink_to(outside)
        with self.assertRaises(ValueError):
            await self.data.ainspect_quarantine('samples')
        self.assertTrue(entry.is_symlink())
        self.assertEqual(outside.read_bytes(), b'outside bytes')
        entry.unlink()
        os.mkfifo(entry)
        with self.assertRaisesRegex(ValueError, 'regular files'):
            await self.data.ainspect_quarantine('samples')
        self.assertTrue(entry.exists())
        entry.unlink()
        plan = await self.data.ainspect_quarantine('samples')
        saved = await self.data.aquarantine('samples', expected_revision=plan['version'])
        stray.write_bytes(b'unknown after quarantine')
        with self.assertRaisesRegex(ValueError, 'package entries'):
            await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
        self.assertEqual(stray.read_bytes(), b'unknown after quarantine')
        self.assertEqual((package / 'quarantine/files/payload.bin').read_bytes(), b'damaged bytes')

    async def test_cancelled_quarantine_finishes_atomic_commit(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        entered, finish = threading.Event(), threading.Event()
        copy = self.component._copy_quarantine_file
        def paused(source, destination):
            result = copy(source, destination)
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('copy was not released')
            return result
        with patch.object(self.component, '_copy_quarantine_file', side_effect=paused):
            task = asyncio.create_task(self.data.aquarantine('samples', expected_revision=plan['version']))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                task.cancel()
            finally:
                finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await self.data.aquarantine_snapshot('samples')
        self.assertFalse((package / 'files').exists())
        self.assertEqual((package / 'quarantine/files/payload.bin').read_bytes(), b'damaged bytes')

    async def test_duplicate_quarantine_and_stale_delete_after_recreation(self):
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        results = await asyncio.gather(*(self.data.aquarantine('samples', expected_revision=plan['version'])
                                        for _ in range(2)), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        saved = await self.data.aquarantine_snapshot('samples')
        with self.assertRaises(ValueError):
            await self.data.adelete_quarantined('samples', expected_revision=None)
        await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
        await self.data.acreate({'title': 'Dataset', 'metadata': {'temporary': True}}, identifier='samples')
        await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        recreated = await self.data.aquarantine('samples', expected_revision=plan['version'])
        self.assertNotEqual(saved['data']['id'], recreated['data']['id'])
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await self.data.adelete_quarantined('samples', expected_revision=saved['version'])
        self.assertEqual(await self.data.aquarantine_snapshot('samples'), recreated)
        self.assertEqual((package / 'quarantine/files/payload.bin').read_bytes(), b'damaged bytes')

    async def test_unfinished_resume_blocks_quarantine_without_changing_checkpoint_or_effects(self):
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        from llm.engines.graph import GraphEngine
        from llm.core.models import RunStatus
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.components.aselect(['data', 'workflows'])
        effects = self.root / 'effects'
        async def work(node):
            with effects.open('a') as stream:
                stream.write('called\n')
            return {}
        self.app.engines.register('graph', GraphEngine(handlers={'work': work}))
        graph = (WorkflowGraph(entry='work').node('work', 'work', pause_before=True)
                 .node('end', 'end').connect('work', 'end').to_dict())
        await self.project.components.workflows.acreate(graph, identifier='flow')
        session = await self.project.sessions.acreate()
        await self.project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'flow'}}, identifier='graph')
        run = await (await session.run.submit('work', agent_id='graph')).wait()
        self.assertEqual(run.data.status, RunStatus.PAUSED, run.data.error)
        checkpoint = await run.acheckpoint()
        await session.run.shutdown()
        package = await self.damaged()
        plan = await self.data.ainspect_quarantine('samples')
        with self.assertRaisesRegex(ValueError, 'unfinished Resume'):
            await self.data.aquarantine('samples', expected_revision=plan['version'])
        self.assertFalse(effects.exists())
        self.assertFalse((package / 'quarantine').exists())
        self.assertEqual((package / 'files/payload.bin').read_bytes(), b'damaged bytes')
        self.assertEqual(await run.acheckpoint(), checkpoint)
        self.assertEqual((await run.aget_data()).status, RunStatus.PAUSED)

    async def test_process_death_preserves_original_or_complete_quarantine_and_delete(self):
        code = textwrap.dedent('''
            import asyncio, json, os, sys
            from pathlib import Path
            from llm.llm import LargeLanguageModel, DataComponent
            from llm.components.data import component as data_module
            from llm.components import base, files
            from llm.services.infrastructure import transactions
            directory, operation, boundary = sys.argv[1:]
            root = Path(directory)
            async def main():
                component = DataComponent()
                async with LargeLanguageModel(root / 'workspace', components=[component]) as app:
                    project = await app.projects.acreate(components=['data'])
                    data = project.components['data']
                    await data.acreate({'title': 'source'}, identifier='source')
                    initial = await data.asnapshot('source')
                    await data.awrite_file('source', 'payload', b'original', expected_revision=initial['version'])
                    package = project.paths.root / 'data/source'
                    (package / 'files/payload').write_bytes(b'damaged bytes')
                    plan = await data.ainspect_quarantine('source')
                    if operation == 'delete':
                        plan = await data.aquarantine('source', expected_revision=plan['version'])
                    paths = [project.paths.root / 'data/records.manifest.json', *package.rglob('*')]
                    before = {str(path.relative_to(project.paths.root)): path.read_bytes().hex()
                              for path in paths if path.is_file()}
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id, 'before': before}))
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    copy, write = component._copy_quarantine_file, data_module.atomic_json
                    remove, delete, manifest = data_module.remove_named_tree, files.remove_named_tree, base.atomic_json
                    journal_write = transactions._write
                    def crash_copy(*args):
                        result = copy(*args)
                        if boundary == 'copy':
                            crash('copy')
                        return result
                    def crash_write(path, value):
                        write(path, value)
                        if boundary == 'receipt':
                            crash('receipt')
                    def crash_remove(*args):
                        remove(*args)
                        if boundary == 'source':
                            crash('source')
                    def crash_delete(*args):
                        delete(*args)
                        if boundary == 'tree':
                            crash('tree')
                    def crash_manifest(path, value):
                        manifest(path, value)
                        if boundary == 'manifest' and path.name == component.RECORDS_MANIFEST:
                            crash('manifest')
                    def crash_journal(path, value):
                        journal_write(path, value)
                        if boundary == 'marker' and path.name == 'COMMITTED':
                            crash('marker')
                    component._copy_quarantine_file = crash_copy
                    data_module.atomic_json, data_module.remove_named_tree = crash_write, crash_remove
                    files.remove_named_tree, base.atomic_json = crash_delete, crash_manifest
                    transactions._write = crash_journal
                    if operation == 'delete':
                        await data.adelete_quarantined('source', expected_revision=plan['version'])
                    else:
                        await data.aquarantine('source', expected_revision=plan['version'])
                    crash('committed')
            asyncio.run(main())
        ''')
        for operation, boundaries in (('quarantine', ('copy', 'receipt', 'source', 'marker', 'committed')),
                                      ('delete', ('tree', 'manifest', 'marker', 'committed'))):
            for boundary in boundaries:
                with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    child = await asyncio.to_thread(subprocess.run,
                        [sys.executable, '-c', code, directory, operation, boundary],
                        timeout=20, capture_output=True, text=True)
                    self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                    self.assertEqual((root / 'reached').read_text(), boundary)
                    context = json.loads((root / 'context.json').read_text())
                    async with LargeLanguageModel(root / 'workspace', components=[DataComponent()]) as app:
                        project = await app.projects.aload(context['project_id'])
                        data = project.components['data']
                        package = project.paths.root / 'data/source'
                        paths = [project.paths.root / 'data/records.manifest.json', *package.rglob('*')]
                        after = {str(path.relative_to(project.paths.root)): path.read_bytes().hex()
                                 for path in paths if path.is_file()}
                        if boundary not in ('marker', 'committed'):
                            self.assertEqual(after, context['before'])
                        elif operation == 'delete':
                            self.assertFalse(package.exists())
                            self.assertEqual(await data.alist(), {})
                            continue
                        else:
                            await data.aquarantine_snapshot('source')
                        preserved = package / ('quarantine/files' if operation == 'delete'
                            or boundary in ('marker', 'committed') else 'files') / 'payload'
                        self.assertEqual(preserved.read_bytes(), b'damaged bytes')
                        with self.assertRaises(ValueError):
                            await data.aload('source')
                        with self.assertRaises(FileExistsError):
                            await data.acreate({'title': 'replacement'}, identifier='source')


class DataQuarantineBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_quarantine_delete_and_restart_never_reset_project_quota(self):
        from llm.llm import LoopEngine
        from tests.llm.test_usage_deletion import UsageDeletionTests
        app, project, session, model = await UsageDeletionTests.create(self, {'project_max_calls': 1})
        app.project_manager.components.register(DataComponent())
        await project.components.aselect(['data'])
        first = await UsageDeletionTests.run_once(self, session)
        self.assertEqual(first.data.status, 'completed', first.data.error)
        await session.run.shutdown()
        record = first.data.paths.root / 'run.json'
        evidence = record.read_bytes()
        data = project.components['data']
        await data.acreate({'title': 'temporary'}, identifier='temporary')
        initial = await data.asnapshot('temporary')
        await data.awrite_file('temporary', 'payload', b'source', expected_revision=initial['version'])
        package = project.paths.root / 'data/temporary'
        (package / 'files/payload').write_bytes(b'damaged')
        plan = await data.ainspect_quarantine('temporary')
        saved = await data.aquarantine('temporary', expected_revision=plan['version'])
        self.assertEqual((await project.amodel_usage())['call_count'], 1)
        await data.adelete_quarantined('temporary', expected_revision=saved['version'])
        self.assertFalse(package.exists())
        self.assertEqual(record.read_bytes(), evidence)
        self.assertEqual((await project.amodel_usage())['call_count'], 1)
        await app.shutdown()
        async with LargeLanguageModel(project.paths.root.parent.parent, components=[DataComponent()],
                                      engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}) as reopened:
            project = await reopened.projects.aload(project.id)
            other = await project.sessions.acreate()
            blocked = await UsageDeletionTests.run_once(self, other)
            self.assertEqual(blocked.data.error_code, 'usage_limit')
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(record.read_bytes(), evidence)
            self.assertEqual((await project.amodel_usage())['call_count'], 1)
