"""Consolidation evidence must not disappear or falsely complete recovery."""
import json
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.memory import MemoryComponent, MemoryConflictError
from llm.components.memory.consolidation import MemoryConsolidation
from llm.services.infrastructure.storage import revision_token


class ConsolidationLossTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.component = MemoryComponent()
        self.app = LargeLanguageModel(self.root / 'workspace', components=[self.component], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=['memory'])
        self.memory = self.project.components.memory
        await self.memory.acreate({'content': 'old'}, identifier='old')
        old = await self.memory.aload('old')
        await self.memory.acreate({'content': 'reviewed', 'status': 'candidate',
            'replaces': [{'id': 'old', 'generation': old['generation'], 'revision': old['revision']}]}, identifier='proposal')
        self.proposal = await self.memory.aload('proposal')
        self.storage = self.project.paths.root / 'memory' / 'consolidations'

    async def apply(self):
        return await self.memory.aconsolidate('proposal', expected_generation=self.proposal['generation'],
                                             expected_revision=self.proposal['revision'])

    async def pending(self):
        # Preserve the normal writer's journal at its recovery handoff. This is
        # fault injection, not a claim about a real process-death commit state.
        with patch.object(MemoryConsolidation, '_recover', return_value=None):
            await self.apply()
        path, = (self.storage / 'pending').glob('*.json')
        return path

    async def test_lost_completed_receipt_is_not_accepted_in_backup(self):
        receipt = await self.apply()
        path = self.storage / 'receipts' / (receipt['id'] + '.json')
        path.unlink()
        with self.assertRaises((ValueError, OSError)):
            await self.project.abackup(self.root / 'backup')
        self.assertFalse((self.root / 'backup').exists())
        self.assertFalse(path.exists())

    async def test_lost_pending_journal_cannot_release_read_and_write_barrier(self):
        path = await self.pending()
        path.unlink()
        with self.assertRaises((ValueError, OSError)):
            await self.memory.aload('proposal')
        self.assertFalse(path.exists())

    async def test_empty_recovery_entries_cannot_create_a_false_completion_receipt(self):
        path = await self.pending()
        journal = json.loads(path.read_bytes())
        journal['entries'] = []
        path.write_text(json.dumps(journal))
        before = path.read_bytes()
        rejected = False
        try:
            await self.memory.arecover_consolidations()
        except (ValueError, OSError):
            rejected = True
        receipt = self.storage / 'receipts' / (journal['id'] + '.json')
        self.assertFalse(receipt.exists(), 'malformed journal created a completion receipt without applying its proposal')
        self.assertTrue(rejected)
        self.assertEqual(path.read_bytes(), before)


class ConsolidationStorageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ConsolidationLossTests.asyncSetUp
    apply = ConsolidationLossTests.apply
    pending = ConsolidationLossTests.pending

    def snapshot(self):
        root = self.storage.parent
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}

    async def test_pending_and_receipt_loss_never_release_read_recovery_or_backup(self):
        pending = await self.pending()
        for phase in ('pending', 'completed'):
            if phase == 'completed':
                receipt, = await self.memory.arecover_consolidations()
                record = self.storage / 'receipts' / (receipt['id'] + '.json')
            else:
                record = pending
            saved = self.root / ('saved-' + phase)
            shutil.copytree(self.storage, saved)
            for path in (record, self.storage / MemoryConsolidation.MANIFEST,
                         self.storage / 'pending', self.storage / 'receipts'):
                with self.subTest(phase=phase, path=path.name):
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
                    before = self.snapshot()
                    for operation in (lambda: self.memory.aload('proposal'),
                                      lambda: self.memory.acreate({'content': 'new'}, identifier='new'),
                                      self.memory.apending_consolidations, self.memory.arecover_consolidations,
                                      lambda: self.project.abackup(self.root / 'backup'),
                                      lambda: self.project.aclone()):
                        with self.assertRaises((ValueError, OSError)):
                            await operation()
                    self.assertEqual(self.snapshot(), before)
                    self.assertFalse(path.exists())
                    self.assertFalse((self.root / 'backup').exists())
                    self.assertEqual(len(await self.app.projects.alist()), 1)
                    shutil.rmtree(self.storage)
                    shutil.copytree(saved, self.storage)

    async def test_disabled_storage_and_invalid_manifests_are_not_adopted(self):
        receipt = await self.apply()
        manifest_path = self.storage / MemoryConsolidation.MANIFEST
        original = json.loads(manifest_path.read_bytes())
        cases = [{k: v for k, v in original.items() if k != key} for key in original]
        cases += [{**original, 'schema_version': value} for value in (None, True, 2, '1')]
        cases += [{**original, 'project_id': 'other'}, {**original, 'component': 'other'},
                  {**original, 'pending': None}, {**original, 'receipts': None},
                  {**original, 'receipts': {}}, {**original, 'pending': original['receipts']},
                  {**original, 'receipts': {receipt['id']: None}}]
        for value in cases:
            with self.subTest(value=value):
                manifest_path.write_text(json.dumps(value))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    await self.memory.alist()
                with self.assertRaises(ValueError):
                    await self.memory.arecover_consolidations()
                self.assertEqual(self.snapshot(), before)
        manifest_path.write_text(json.dumps(original))
        await self.project.components.aremove('memory')
        path = self.storage / 'receipts' / (receipt['id'] + '.json')
        path.unlink()
        before = self.snapshot()
        for operation in (lambda: self.project.components.aselect(['memory']),
                          lambda: self.project.abackup(self.root / 'backup'),
                          lambda: self.project.components.aremove('memory', permanent=True)):
            with self.assertRaises((ValueError, OSError)):
                await operation()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(path.exists())

    async def test_matching_hash_cannot_recover_unreviewed_or_incomplete_changes(self):
        await self.memory.acreate({'content': 'unrelated'}, identifier='other')
        owner = MemoryConsolidation(self.component, await self.project.aget_data())
        other = owner._envelope('other')
        path = await self.pending()
        original = json.loads(path.read_bytes())
        manifest_path = self.storage / MemoryConsolidation.MANIFEST
        manifest = json.loads(manifest_path.read_bytes())
        cases = [None, {}]
        cases += [{k: v for k, v in original.items() if k != key} for key in original]
        cases += [{**original, 'identity': 'other'}, {**original, 'entries': []},
                  {**original, 'entries': [original['entries'][0]]},
                  {**original, 'entries': [*original['entries'], original['entries'][1]]},
                  {**original, 'superseded': ['proposal']}, {**original, 'source': {}},
                  {**original, 'source': {**original['source'], 'operation_id': 'other'}}]
        changed = deepcopy(original)
        changed['entries'][0]['after']['record']['content'] = 'unreviewed'
        changed['entries'][0]['after']['history'][-1]['data']['content'] = 'unreviewed'
        cases.append(changed)
        changed = deepcopy(original)
        changed['superseded'] = ['other']
        changed['entries'][1] = {'id': 'other', 'before': owner._digest(other),
            'after': owner._after('other', other, original['proposal'], original['source'],
                                 original['entries'][1]['after']['record']['updated_at'], original['id'])}
        cases.append(changed)
        for value in cases:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                manifest['pending'][original['id']] = revision_token(value)
                manifest_path.write_text(json.dumps(manifest))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    await self.memory.arecover_consolidations()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(list((self.storage / 'receipts').iterdir()), [])

    async def test_matching_hash_cannot_hide_invalid_receipt_fields(self):
        original = await self.apply()
        path = self.storage / 'receipts' / (original['id'] + '.json')
        manifest_path = self.storage / MemoryConsolidation.MANIFEST
        manifest = json.loads(manifest_path.read_bytes())
        cases = [None, {}]
        cases += [{k: v for k, v in original.items() if k != key} for key in original]
        cases += [{**original, 'status': 'pending'}, {**original, 'id': 'other'},
                  {**original, 'superseded': ['old', 'old']}, {**original, 'source': None}]
        for value in cases:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                manifest['receipts'][original['id']] = revision_token(value)
                manifest_path.write_text(json.dumps(manifest))
                before = self.snapshot()
                for operation in (self.memory.alist, self.memory.apending_consolidations,
                                  lambda: self.project.abackup(self.root / 'backup')):
                    with self.assertRaises(ValueError):
                        await operation()
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.root / 'backup').exists())

    async def test_valid_partial_recovery_is_idempotent_and_clone_keeps_receipt_provenance(self):
        path = await self.pending()
        journal = json.loads(path.read_bytes())
        with self.assertRaises(MemoryConflictError):
            await self.project.aclone()
        project_data = await self.project.aget_data()
        with self.app.project_manager.ownership.scope():
            self.component._write(project_data, 'proposal', journal['entries'][0]['after'])
        receipt, = await self.memory.arecover_consolidations()
        self.assertEqual(await self.memory.arecover_consolidations(), [])
        self.assertEqual(await self.memory.apending_consolidations(), [])
        for name in ('proposal', 'old'):
            self.assertEqual(len(await self.memory.ahistory(name)), 2)
        self.assertTrue((await self.memory.aload('old', include_deleted=True))['deleted'])
        clone = await self.project.aclone()
        copied = clone.paths.root / 'memory' / 'consolidations' / 'receipts' / (receipt['id'] + '.json')
        self.assertEqual(json.loads(copied.read_bytes()), receipt)
        self.assertEqual(receipt['source']['project_id'], self.project.id)
        self.assertEqual(await clone.components.memory.ahistory('proposal'), await self.memory.ahistory('proposal'))
        copied.unlink()
        with self.assertRaises(ValueError):
            await clone.components.memory.alist()
        self.assertEqual((await self.memory.aload('proposal'))['status'], 'confirmed')

    async def test_nonregular_and_undeclared_journals_never_enter_recovery(self):
        path = await self.pending()
        for entry in (path, self.storage / MemoryConsolidation.MANIFEST):
            content = entry.read_bytes()
            for kind in ('link', 'fifo', 'directory'):
                with self.subTest(entry=entry.name, kind=kind):
                    entry.unlink()
                    target = self.root / 'outside.json'
                    target.write_bytes(content)
                    if kind == 'link':
                        entry.symlink_to(target)
                    elif kind == 'fifo':
                        os.mkfifo(entry)
                    else:
                        entry.mkdir()
                    with self.assertRaises(ValueError):
                        await self.memory.arecover_consolidations()
                    self.assertEqual(target.read_bytes(), content)
                    entry.rmdir() if kind == 'directory' else entry.unlink()
                    entry.write_bytes(content)
        unknown = path.with_name('unknown.json')
        unknown.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            await self.memory.arecover_consolidations()
        self.assertEqual(unknown.read_bytes(), path.read_bytes())

    async def test_failure_after_each_persistent_write_rolls_back_all_reviewed_records(self):
        from llm.components.memory import consolidation
        write, unlink, record = consolidation.atomic_json, consolidation.unlink_file, self.component._write
        for boundary in ('journal', 'pending_manifest', 'proposal', 'old', 'receipt', 'unlink', 'manifest'):
            with self.subTest(boundary=boundary):
                before = self.snapshot()
                def fail_write(path, value):
                    write(path, value)
                    if (boundary == 'journal' and path.parent.name == 'pending'
                            or boundary == 'receipt' and path.parent.name == 'receipts'
                            or boundary == 'pending_manifest' and path.name == MemoryConsolidation.MANIFEST and value['pending']
                            or boundary == 'manifest' and path.name == MemoryConsolidation.MANIFEST and value['receipts']):
                        raise OSError('consolidation flush failed')
                def fail_record(project, identifier, value):
                    record(project, identifier, value)
                    if boundary == identifier:
                        raise OSError('record flush failed')
                def fail_unlink(path):
                    unlink(path)
                    if boundary == 'unlink':
                        raise OSError('pending unlink failed')
                with patch.object(consolidation, 'atomic_json', side_effect=fail_write), \
                     patch.object(consolidation, 'unlink_file', side_effect=fail_unlink), \
                     patch.object(self.component, '_write', side_effect=fail_record), self.assertRaises(OSError):
                    await self.apply()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual((await self.memory.aload('proposal'))['status'], 'candidate')
                self.assertFalse((await self.memory.aload('old'))['deleted'])

    async def test_process_death_preserves_apply_and_explicit_recovery_commit_boundaries(self):
        code = textwrap.dedent('''
            import asyncio, json, os, sys
            from pathlib import Path
            from unittest.mock import patch
            from llm.llm import LargeLanguageModel
            from llm.components.memory import MemoryComponent, consolidation
            directory, operation, boundary = sys.argv[1:]
            root = Path(directory)
            async def main():
                component = MemoryComponent()
                async with LargeLanguageModel(root / 'workspace', components=[component], engines={}) as app:
                    project = await app.projects.acreate(components=['memory'])
                    memory = project.components.memory
                    await memory.acreate({'content': 'old'}, identifier='old')
                    old = await memory.aload('old')
                    await memory.acreate({'content': 'reviewed', 'status': 'candidate',
                        'replaces': [{'id': 'old', 'generation': old['generation'], 'revision': 1}]}, identifier='proposal')
                    proposal = await memory.aload('proposal')
                    async def apply():
                        return await memory.aconsolidate('proposal', expected_generation=proposal['generation'], expected_revision=1)
                    if operation == 'recover':
                        with patch.object(consolidation.MemoryConsolidation, '_recover', return_value=None):
                            await apply()
                    storage = project.paths.root / 'memory'
                    before = {str(p.relative_to(storage)): p.read_bytes().hex() for p in storage.rglob('*') if p.is_file()}
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id, 'before': before}))
                    write, unlink, record = consolidation.atomic_json, consolidation.unlink_file, component._write
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    def crash_write(path, value):
                        write(path, value)
                        if boundary == 'journal' and path.parent.name == 'pending':
                            crash('journal')
                        if boundary == 'receipt' and path.parent.name == 'receipts':
                            crash('receipt')
                        if path.name == consolidation.MemoryConsolidation.MANIFEST:
                            if boundary == 'pending_manifest' and value['pending']:
                                crash('pending_manifest')
                            if boundary == 'manifest' and value['receipts']:
                                crash('manifest')
                    def crash_record(project, identifier, value):
                        record(project, identifier, value)
                        if boundary == identifier:
                            crash(identifier)
                    def crash_unlink(path):
                        unlink(path)
                        if boundary == 'unlink':
                            crash('unlink')
                    consolidation.atomic_json, consolidation.unlink_file, component._write = crash_write, crash_unlink, crash_record
                    await (memory.arecover_consolidations() if operation == 'recover' else apply())
                    crash('committed')
            asyncio.run(main())
        ''')
        for operation in ('apply', 'recover'):
            boundaries = ('journal', 'pending_manifest', 'proposal', 'old', 'receipt', 'unlink', 'manifest', 'committed') if operation == 'apply' else (
                'proposal', 'old', 'receipt', 'unlink', 'manifest', 'committed')
            for boundary in boundaries:
                with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    child = subprocess.run([sys.executable, '-c', code, directory, operation, boundary],
                                           timeout=20, capture_output=True, text=True)
                    self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                    self.assertEqual((root / 'reached').read_text(), boundary)
                    context = json.loads((root / 'context.json').read_text())
                    async with LargeLanguageModel(root / 'workspace', components=[MemoryComponent()], engines={}) as app:
                        project = await app.projects.aload(context['project_id'])
                        memory = project.components.memory
                        storage = project.paths.root / 'memory'
                        pending = await memory.apending_consolidations()
                        after = {str(p.relative_to(storage)): p.read_bytes().hex() for p in storage.rglob('*') if p.is_file()}
                        if boundary != 'committed':
                            self.assertEqual(after, context['before'])
                            if operation == 'recover':
                                self.assertEqual(len(pending), 1)
                                with self.assertRaises(MemoryConflictError):
                                    await memory.alist()
                                await memory.arecover_consolidations()
                            else:
                                self.assertEqual(pending, [])
                                self.assertEqual((await memory.aload('proposal'))['status'], 'candidate')
                                self.assertFalse((await memory.aload('old'))['deleted'])
                                continue
                        self.assertEqual(await memory.apending_consolidations(), [])
                        self.assertEqual((await memory.aload('proposal'))['status'], 'confirmed')
                        self.assertTrue((await memory.aload('old', include_deleted=True))['deleted'])
                        for name in ('proposal', 'old'):
                            self.assertEqual(len(await memory.ahistory(name)), 2)
                        self.assertEqual(len(list((storage / 'consolidations' / 'receipts').iterdir())), 1)
                        self.assertEqual(await memory.arecover_consolidations(), [])
