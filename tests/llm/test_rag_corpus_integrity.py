"""Published RAG corpus fields protect original data and model effects."""
import json
from copy import deepcopy
import unittest

from tests.llm import test_rag_storage as storage
from tests.llm import test_rag_job_state_integrity as state
from llm.components.rag import RAGComponent


class CorpusFieldTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = storage.RAGStorageTests.asyncSetUp
    add = storage.RAGStorageTests.add
    snapshot_files = storage.RAGStorageTests.snapshot_files

    async def damage(self, change):
        await self.add(identifier='original')
        root = self.project.paths.root / 'rag'
        generation = json.loads((root / 'active.json').read_bytes())['generation']
        path = root / 'generations' / generation / 'corpus.json'
        value = json.loads(path.read_bytes())
        change(value)
        path.write_text(json.dumps(value))
        before = self.snapshot_files()
        with self.assertRaises((ValueError, OSError)):
            await self.project.abackup(self.root / 'invalid-backup')
        self.assertEqual(self.snapshot_files(), before)
        self.assertFalse((self.root / 'invalid-backup').exists())

    async def test_missing_corpus_profile_is_not_a_valid_backup(self):
        await self.damage(lambda value: value.pop('profile'))

    async def test_missing_published_document_title_is_not_a_valid_backup(self):
        await self.damage(lambda value: value['documents']['original'].pop('title'))


class CorpusContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await state.JobStateTests.asyncSetUp(self)
        await self.rag.arun_job(self.job['id'])
        self.identifier = self.job['document_id']
        root = self.project.paths.root / 'rag'
        generation = json.loads((root / 'active.json').read_bytes())['generation']
        self.corpus = root / 'generations' / generation / 'corpus.json'
        self.original = json.loads(self.corpus.read_bytes())
        self.assertEqual(self.effects.read_text(), 'called\n')

    snapshot_files = storage.RAGStorageTests.snapshot_files

    async def reject(self, value):
        self.corpus.write_text(json.dumps(value))
        before = self.snapshot_files()
        for operation in (self.rag.alist_documents, self.rag.acompact,
                          lambda: self.rag.aget_document(self.identifier),
                          lambda: self.rag.aadd_document(title='New', content='backup'),
                          lambda: self.rag.aupdate_document(self.identifier, title='Changed'),
                          lambda: self.project.components.aselect(['rag']),
                          lambda: self.project.abackup(self.root / 'invalid-backup'), self.project.aclone):
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(self.snapshot_files(), before)
            self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertFalse((self.root / 'invalid-backup').exists())
        self.assertEqual(len(await self.app.projects.alist()), 1)

    async def test_each_published_document_field_protects_all_consumers_before_new_effects(self):
        for key in RAGComponent.DOCUMENT_FIELDS:
            with self.subTest(key=key):
                value = deepcopy(self.original)
                del value['documents'][self.identifier][key]
                await self.reject(value)

    async def test_corpus_requires_exact_root_fields_document_mapping_and_matching_profile(self):
        cases = [None, [], {}, {**self.original, 'unexpected': True}]
        for key in self.original:
            value = deepcopy(self.original)
            del value[key]
            cases.append(value)
        for key, replacement in (('documents', []), ('documents', None), ('documents', {}),
                                 ('profile', None), ('profile', {}),
                                 ('profile', {**self.original['profile'], 'model': 'changed'}),
                                 ('profile', {**self.original['profile'], 'dimensions': 999})):
            cases.append({**self.original, key: replacement})
        cases.append({**self.original, 'documents': {self.identifier: None}})
        cases.append({**self.original, 'documents': {'wrong-id': self.original['documents'][self.identifier]}})
        for position, value in enumerate(cases):
            with self.subTest(case=position):
                await self.reject(value)

    async def test_cache_hit_never_hides_required_field_loss_or_mutated_returned_data(self):
        from tests.llm.configuration_fixtures import rag_settings
        await self.rag.aconfigure(rag_settings({'config': {'snapshot_cache_max_bytes': 1000000}}))
        returned = await self.rag.aget_document(self.identifier)
        returned.pop('title')
        self.assertEqual((await self.rag.aget_document(self.identifier))['title'], 'Manual')
        value = deepcopy(self.original)
        del value['documents'][self.identifier]['vectors']
        await self.reject(value)

    async def test_owner_rejects_invalid_publication_before_creating_a_generation(self):
        project = await self.project.aget_data()
        component = self.component.with_config(project)
        snapshot = component.snapshot(project)
        before = self.snapshot_files()
        invalid = deepcopy(snapshot['documents'])
        del invalid[self.identifier]['content']
        with self.app.project_manager.ownership.scope():
            with self.assertRaises(ValueError):
                component.publish(project, snapshot, invalid)
        self.assertEqual(self.snapshot_files(), before)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_normal_metadata_update_clone_backup_and_explicit_empty_corpus(self):
        updated = await self.rag.aupdate_document(self.identifier, title='Revised', metadata={'tag': 'new'}, description='Guide')
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(self.effects.read_text(), 'called\n')
        clone = await self.project.aclone()
        self.assertEqual((await clone.components.rag.aget_document(self.identifier))['title'], 'Revised')
        await self.project.abackup(self.root / 'backup')
        await self.rag.adelete_document(self.identifier)
        self.assertEqual(await self.rag.alist_documents(), [])
        self.assertEqual(self.effects.read_text(), 'called\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
