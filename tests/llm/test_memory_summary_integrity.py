"""Memory summary absence must distinguish explicit clear from missing stored data."""
from pathlib import Path
from copy import deepcopy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.memory import MemoryComponent
from llm.components.memory.summary_storage import SummaryStorage
from llm.services.infrastructure.storage import revision_token


class SummaryLossTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.component = MemoryComponent()
        self.app = LargeLanguageModel(self.root, components=[self.component], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=['memory'])
        self.session = await self.project.sessions.acreate()
        self.memory = self.project.components.memory
        self.original = await self.publish('original')
        self.path = self.component._summary_path(await self.project.aget_data(), self.session.id)
        self.path.unlink()

    async def publish(self, content):
        snapshot = await self.memory._async_call(self.memory._processing_snapshot, self.session.id)
        value = {'content': content, 'metadata': {'coverage_count': 1, 'coverage_hash': 'fixture',
            'profile': 'fixture', 'through_message_id': 'last', 'first_message_id': 'first'}}
        return await self.memory._async_call(self.memory._publish_summary, snapshot, value,
                                            session_id=self.session.id, source={})

    async def test_lost_summary_is_not_an_explicitly_empty_context(self):
        with self.assertRaises((ValueError, OSError)):
            await self.memory.asummary(self.session.id)
        self.assertFalse(self.path.exists())

    async def test_lost_summary_cannot_reissue_generation_with_revision_one(self):
        rejected = False
        try:
            await self.publish('replacement')
        except (ValueError, OSError):
            rejected = True
        self.assertTrue(rejected, 'lost summary was replaced with a fresh generation/revision')
        self.assertFalse(self.path.exists())


class SummaryStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.component = MemoryComponent()
        self.app = LargeLanguageModel(self.root / 'workspace', components=[self.component], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=['memory'])
        self.session = await self.project.sessions.acreate()
        self.memory = self.project.components.memory
        self.project_data = await self.project.aget_data()
        self.storage = SummaryStorage(self.project_data, self.component)
        self.original = await self.publish('original')
        self.path = self.storage.path(self.session.id)

    publish = SummaryLossTests.publish

    def snapshot(self):
        return {str(path.relative_to(self.storage.manifest.parent)): path.read_bytes()
                for path in self.storage.manifest.parent.rglob('*') if path.is_file()}

    async def clear(self):
        await self.memory.aclear_summary(self.session.id, expected_generation=self.original['generation'],
                                        expected_revision=self.original['revision'])

    async def test_declared_loss_blocks_all_readers_writers_references_and_portability(self):
        saved = self.root / 'saved'
        shutil.copytree(self.storage.manifest.parent, saved)
        for path in (self.path, self.storage.manifest, self.storage.directory):
            with self.subTest(path=path.name):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
                before = self.snapshot()
                for operation in (lambda: self.memory.asummary(self.session.id),
                                  lambda: self.publish('replacement'), self.clear,
                                  lambda: self.project.abackup(self.root / 'backup'),
                                  lambda: self.project.aclone()):
                    with self.assertRaises((ValueError, OSError)):
                        await operation()
                for operation in (self.component.history_references, self.component.initialize,
                                  self.component.validate_backup):
                    with self.assertRaises((ValueError, OSError)):
                        operation(self.project_data)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(path.exists())
                self.assertFalse((self.root / 'backup').exists())
                self.assertEqual(len(await self.app.projects.alist()), 1)
                shutil.rmtree(self.storage.manifest.parent)
                shutil.copytree(saved, self.storage.manifest.parent)

    async def test_disabled_owner_cannot_hide_lost_summary_or_reinitialize(self):
        await self.project.components.aremove('memory')
        self.path.unlink()
        before = self.snapshot()
        for operation in (lambda: self.project.components.aselect(['memory']),
                          lambda: self.project.abackup(self.root / 'backup'),
                          lambda: self.project.components.aremove('memory', permanent=True)):
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.path.exists())
        self.assertFalse((self.root / 'backup').exists())

    async def test_manifest_fields_and_owner_cannot_be_defaulted_or_adopted(self):
        original = json.loads(self.storage.manifest.read_bytes())
        cases = [{k: v for k, v in original.items() if k != key} for key in original]
        cases += [{**original, 'schema_version': value} for value in (None, True, 2, '1')]
        cases += [{**original, 'project_id': 'other'}, {**original, 'component': 'rag'},
                  {**original, 'summaries': None}, {**original, 'summaries': {}},
                  {**original, 'summaries': {self.session.id: None}},
                  {**original, 'extra': 'unsupported'}]
        for value in cases:
            with self.subTest(value=value):
                self.storage.manifest.write_text(json.dumps(value))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    await self.memory.asummary(self.session.id)
                with self.assertRaises(ValueError):
                    await self.publish('replacement')
                self.assertEqual(self.snapshot(), before)
        self.storage.manifest.write_text(json.dumps(original))
        unknown = self.storage.directory / 'unknown.json'
        unknown.write_bytes(self.path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            await self.memory.asummary(self.session.id)
        self.assertEqual(unknown.read_bytes(), self.path.read_bytes())

    async def test_matching_hash_does_not_restore_required_summary_fields(self):
        manifest = json.loads(self.storage.manifest.read_bytes())
        original = deepcopy(self.original)
        cases = [None, [], {k: v for k, v in original.items() if k != 'metadata'}]
        cases += [{k: v for k, v in original.items() if k != key}
                  for key in ('generation', 'revision', 'session_id', 'content', 'source')]
        cases += [{**original, 'generation': 'short'}, {**original, 'revision': True},
                  {**original, 'session_id': 'other'}, {**original, 'metadata': None},
                  {**original, 'metadata': []}, {**original, 'source': None}]
        metadata = original['metadata']
        cases += [{**original, 'metadata': {k: v for k, v in metadata.items() if k != key}}
                  for key in metadata]
        for value in cases:
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value))
                manifest['summaries'][self.session.id] = revision_token(value)
                self.storage.manifest.write_text(json.dumps(manifest))
                before = self.snapshot()
                for operation in (lambda: self.memory.asummary(self.session.id),
                                  lambda: self.publish('replacement'), self.clear):
                    with self.assertRaises(ValueError):
                        await operation()
                self.assertEqual(self.snapshot(), before)

    async def test_links_and_nonregular_evidence_are_rejected_without_reading(self):
        for path in (self.storage.manifest, self.path):
            original = path.read_bytes()
            for kind in ('link', 'fifo', 'directory'):
                with self.subTest(path=path.name, kind=kind):
                    path.unlink()
                    target = self.root / 'outside.json'
                    target.write_bytes(original)
                    if kind == 'link':
                        path.symlink_to(target)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    else:
                        path.mkdir()
                    with self.assertRaises(ValueError):
                        await self.memory.asummary(self.session.id)
                    self.assertEqual(target.read_bytes(), original)
                    path.rmdir() if kind == 'directory' else path.unlink()
                    path.write_bytes(original)

    async def test_create_update_clear_failures_restore_summary_and_declaration(self):
        from llm.components.memory import summary_storage
        write, unlink = summary_storage.atomic_json, summary_storage.unlink_file
        for operation in ('update', 'clear', 'create'):
            if operation == 'create':
                await self.clear()
                self.assertIsNone(await self.memory.asummary(self.session.id))
            for boundary in ('record', 'manifest'):
                with self.subTest(operation=operation, boundary=boundary):
                    before = self.snapshot()
                    def fail_write(path, value):
                        write(path, value)
                        if path == (self.path if boundary == 'record' else self.storage.manifest):
                            raise OSError('summary write failed after flush')
                    def fail_unlink(path):
                        unlink(path)
                        if boundary == 'record':
                            raise OSError('summary clear failed after unlink')
                    with patch.object(summary_storage, 'atomic_json', side_effect=fail_write), \
                         patch.object(summary_storage, 'unlink_file', side_effect=fail_unlink), self.assertRaises(OSError):
                        await (self.clear() if operation == 'clear' else self.publish('replacement'))
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(await self.memory.asummary(self.session.id),
                                     None if operation == 'create' else self.original)

    async def test_process_death_recovers_create_update_and_clear_commit_boundaries(self):
        code = textwrap.dedent('''
            import asyncio, json, os, sys
            from pathlib import Path
            from llm.llm import LargeLanguageModel
            from llm.components.memory import MemoryComponent, summary_storage
            directory, operation, boundary = sys.argv[1:]
            root = Path(directory)
            async def main():
                component = MemoryComponent()
                async with LargeLanguageModel(root / 'workspace', components=[component], engines={}) as app:
                    project = await app.projects.acreate(components=['memory'])
                    session = await project.sessions.acreate()
                    memory = project.components.memory
                    owner = summary_storage.SummaryStorage(await project.aget_data(), component)
                    async def publish(content):
                        snapshot = await memory._async_call(memory._processing_snapshot, session.id)
                        value = {'content': content, 'metadata': {'coverage_count': 1, 'coverage_hash': 'fixture',
                            'profile': 'fixture', 'through_message_id': 'last', 'first_message_id': 'first'}}
                        return await memory._async_call(memory._publish_summary, snapshot, value,
                                                        session_id=session.id, source={})
                    old = None if operation == 'create' else await publish('original')
                    before = {str(p.relative_to(owner.manifest.parent)): p.read_bytes().hex()
                              for p in owner.manifest.parent.rglob('*') if p.is_file()}
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id,
                        'session_id': session.id, 'old': old, 'before': before}))
                    write, unlink = summary_storage.atomic_json, summary_storage.unlink_file
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    def crash_write(path, value):
                        write(path, value)
                        if boundary == 'record' and path == owner.path(session.id):
                            crash('record')
                        if boundary == 'manifest' and path == owner.manifest:
                            crash('manifest')
                    def crash_unlink(path):
                        unlink(path)
                        if boundary == 'record':
                            crash('record')
                    summary_storage.atomic_json, summary_storage.unlink_file = crash_write, crash_unlink
                    if operation == 'clear':
                        await memory.aclear_summary(session.id, expected_generation=old['generation'],
                                                    expected_revision=old['revision'])
                    else:
                        await publish('replacement')
                    crash('committed')
            asyncio.run(main())
        ''')
        for operation in ('create', 'update', 'clear'):
            for boundary in ('record', 'manifest', 'committed'):
                with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    child = subprocess.run([sys.executable, '-c', code, directory, operation, boundary],
                                           timeout=20, capture_output=True, text=True)
                    self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                    self.assertEqual((root / 'reached').read_text(), boundary)
                    context = json.loads((root / 'context.json').read_text())
                    component = MemoryComponent()
                    async with LargeLanguageModel(root / 'workspace', components=[component], engines={}) as app:
                        project = await app.projects.aload(context['project_id'])
                        value = await project.components.memory.asummary(context['session_id'])
                        owner = SummaryStorage(await project.aget_data(), component)
                        after = {str(p.relative_to(owner.manifest.parent)): p.read_bytes().hex()
                                 for p in owner.manifest.parent.rglob('*') if p.is_file()}
                        if boundary != 'committed':
                            self.assertEqual(after, context['before'])
                            self.assertEqual(value, context['old'])
                        elif operation == 'clear':
                            self.assertIsNone(value)
                            self.assertEqual(owner.inventory()[1], {})
                        else:
                            self.assertEqual(value['content'], 'replacement')
                            self.assertEqual(value['revision'], 1 if operation == 'create' else 2)
                            if operation == 'update':
                                self.assertEqual(value['generation'], context['old']['generation'])
                        component.history_references(await project.aget_data())
