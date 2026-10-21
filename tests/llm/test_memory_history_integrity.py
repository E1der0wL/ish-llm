"""Exercise stored Memory history semantics independently of byte integrity."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.memory import MemoryComponent
from llm.components import base


class HistoryContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.component = MemoryComponent()
        self.app = LargeLanguageModel(self.root, components=[self.component], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=['memory'])
        self.first = await self.project.sessions.acreate()
        self.second = await self.project.sessions.acreate()
        self.memory = self.project.components.memory
        await self.memory.acreate({'content': 'private source', 'scope': 'session', 'session_id': self.first.id}, identifier='item')
        record = await self.memory.aload('item', session_id=self.first.id)
        await self.memory.aupdate('item', {'content': 'private update'}, session_id=self.first.id,
            expected_revision=record['revision'], expected_generation=record['generation'])
        self.path = self.component._record_path(await self.project.aget_data(), 'item')

    def change(self, modify):
        value = json.loads(self.path.read_bytes())
        modify(value)
        self.path.write_text(json.dumps(value))
        manifest_path = self.path.parent.parent / MemoryComponent.RECORDS_MANIFEST
        manifest = json.loads(manifest_path.read_bytes())
        manifest['records']['item'] = hashlib.sha256(self.path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))

    async def test_history_revision_boolean_is_not_a_stored_integer_revision(self):
        self.change(lambda value: value['history'][0].update(revision=True))
        with self.assertRaises(ValueError):
            await self.memory.ahistory('item', session_id=self.first.id)

    async def test_creation_identity_cannot_change_between_revisions(self):
        def modify(value):
            for record in (value['record'], value['history'][-1]['data']):
                record['created_at'] = '2099-01-01T00:00:00+00:00'
        self.change(modify)
        with self.assertRaises(ValueError):
            await self.memory.aload('item', session_id=self.first.id)

    async def test_stored_scope_change_cannot_expose_another_sessions_memory(self):
        def modify(value):
            for record in (value['record'], value['history'][-1]['data']):
                record['session_id'] = self.second.id
        self.change(modify)
        with self.assertRaises(ValueError):
            await self.memory.aload('item', session_id=self.second.id)

    async def test_restore_history_requires_a_previously_deleted_record(self):
        self.change(lambda value: value['history'][-1].update(operation='restore'))
        with self.assertRaises(ValueError):
            await self.memory.ahistory('item', session_id=self.first.id)


class HistoryStorageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = HistoryContractTests.asyncSetUp
    change = HistoryContractTests.change

    def snapshot(self):
        # Failed writes may append a Project diagnostic log. Compare every
        # Memory-owned file, including the source history and its inventory.
        root = self.project.paths.root / 'memory'
        return {str(path.relative_to(self.project.paths.root)): path.read_bytes()
                for path in root.rglob('*') if path.is_file()}

    async def reject(self, value):
        self.change(lambda stored: (stored.clear(), stored.update(deepcopy(value))))
        before = self.snapshot()
        for operation in (
                lambda: self.memory.ahistory('item', session_id=self.first.id),
                lambda: self.memory.alist(session_id=self.second.id, include_deleted=True),
                lambda: self.project.abackup(self.root / 'invalid-backup'),
                self.project.aclone):
            with self.assertRaises(ValueError):
                await operation()
            self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / 'invalid-backup').exists())

    async def test_matching_hash_requires_complete_history_fields_and_typed_revisions(self):
        original = json.loads(self.path.read_bytes())
        cases = []
        for key in original['history'][0]:
            value = deepcopy(original)
            del value['history'][0][key]
            cases.append(value)
        for key, replacement in (('revision', 1.0), ('revision', True), ('operation', []),
                                 ('source', None), ('at', 'wrong'), ('unexpected', True)):
            value = deepcopy(original)
            value['history'][0][key] = replacement
            cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                await self.reject(value)

    async def test_creation_source_scope_and_generation_remain_immutable(self):
        original = json.loads(self.path.read_bytes())
        for changes in ({'created_at': '2099-01-01T00:00:00+00:00'}, {'source': {'kind': 'different'}},
                        {'scope': 'project', 'session_id': None}, {'session_id': self.second.id},
                        {'generation': 'f' * 32}):
            with self.subTest(changes=changes):
                value = deepcopy(original)
                value['record'].update(changes)
                value['history'][-1]['data'].update(changes)
                await self.reject(value)

    async def test_creation_and_revision_times_require_aware_iso_values(self):
        original = json.loads(self.path.read_bytes())
        for key in ('created_at', 'updated_at'):
            for invalid in ('not-a-time', '2026-01-01', '2026-01-01T00:00:00'):
                with self.subTest(key=key, invalid=invalid):
                    value = deepcopy(original)
                    for record in [value['record'], *(entry['data'] for entry in value['history'])]:
                        record[key] = invalid
                    for entry in value['history']:
                        entry['at'] = entry['data']['updated_at']
                    await self.reject(value)

    async def test_creation_and_deleted_state_transitions_cannot_be_relabelled(self):
        original = json.loads(self.path.read_bytes())
        cases = []
        for index, operation in ((0, 'save'), (0, 'update'), (0, 'delete'), (0, 'restore'),
                                 (1, 'create'), (1, 'delete'), (1, 'restore')):
            value = deepcopy(original)
            value['history'][index]['operation'] = operation
            cases.append(value)
        value = deepcopy(original)
        value['history'][0]['data']['deleted'] = True
        cases.append(value)
        value = deepcopy(original)
        value['record']['deleted'] = value['history'][-1]['data']['deleted'] = True
        cases.append(value)
        value = deepcopy(original)
        value['history'][0]['source'] = {'kind': 'different'}
        cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                await self.reject(value)

    async def test_delete_restore_cannot_rewrite_content_or_unrelated_metadata(self):
        record = await self.memory.aload('item', session_id=self.first.id)
        for operation in (self.memory.adelete, self.memory.arestore):
            record = await operation('item', session_id=self.first.id,
                expected_revision=record['revision'], expected_generation=record['generation'])
            original = json.loads(self.path.read_bytes())
            for changes in ({'content': 'replacement'}, {'metadata': {'unrelated': True}},
                            {'status': 'candidate'}, {'tags': ['unreviewed']}):
                with self.subTest(operation=operation.__name__, changes=changes):
                    value = deepcopy(original)
                    value['record'].update(changes)
                    value['history'][-1]['data'].update(changes)
                    await self.reject(value)
            self.change(lambda value: (value.clear(), value.update(deepcopy(original))))

    async def test_valid_save_delete_restore_and_clone_keep_private_history(self):
        record = await self.memory.aload('item', session_id=self.first.id)
        record = await self.memory.asave('item', {'content': 'saved', 'kind': 'note', 'scope': 'session',
            'session_id': self.first.id, 'status': 'confirmed', 'tags': []}, session_id=self.first.id,
            expected_revision=record['revision'], expected_generation=record['generation'])
        for operation in (self.memory.adelete, self.memory.arestore):
            record = await operation('item', session_id=self.first.id,
                expected_revision=record['revision'], expected_generation=record['generation'])
        history = await self.memory.ahistory('item', session_id=self.first.id)
        self.assertEqual([entry['operation'] for entry in history], ['create', 'update', 'save', 'delete', 'restore'])
        self.assertEqual({entry['data']['created_at'] for entry in history}, {record['created_at']})
        clone = await self.project.aclone()
        self.assertEqual(await clone.components.memory.ahistory('item', session_id=self.first.id), history)
        with self.assertRaises(FileNotFoundError):
            await clone.components.memory.aload('item', session_id=self.second.id)
        # A wall clock adjustment is not an invalid revision ordering.
        def earlier(value):
            value['record']['updated_at'] = value['history'][-1]['data']['updated_at'] = '2000-01-01T00:00:00+00:00'
            value['history'][-1]['at'] = value['record']['updated_at']
        self.change(earlier)
        self.assertEqual((await self.memory.aload('item', session_id=self.first.id))['revision'], 5)

    async def test_failed_record_or_inventory_write_preserves_original_history(self):
        before = self.snapshot()
        record = await self.memory.aload('item', session_id=self.first.id)
        for target in (self.path.name, MemoryComponent.RECORDS_MANIFEST):
            def fail_after_write(path, value):
                original_write(path, value)
                if Path(path).name == target:
                    raise OSError('after Memory file write')
            original_write = base.atomic_json
            with self.subTest(target=target), patch.object(base, 'atomic_json', side_effect=fail_after_write):
                with self.assertRaises(OSError):
                    await self.memory.adelete('item', session_id=self.first.id,
                        expected_revision=record['revision'], expected_generation=record['generation'])
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(await self.memory.aload('item', session_id=self.first.id), record)


if __name__ == '__main__':
    unittest.main(verbosity=2)
