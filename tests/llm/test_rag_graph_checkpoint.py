"""Stored graph receipts must retain canonical evidence before backup and reuse."""
import json
from copy import deepcopy
import unittest
from unittest.mock import patch
from tests.llm import test_rag_checkpoint_integrity as checkpoints
from llm.components.rag import RAGComponent
from llm.services.infrastructure.storage import revision_token

class GraphPayloadProbe(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=checkpoints.CheckpointStorageTests.asyncSetUp
    failed_job=checkpoints.CheckpointStorageTests.failed_job
    snapshot=checkpoints.CheckpointStorageTests.snapshot

    async def test_empty_graph_payload_is_not_a_valid_backup(self):
        identifier=await self.failed_job()
        key='batches/graph_0.json'
        path=self.storage.path(identifier)/key
        value=json.loads(path.read_bytes())
        value['value']={}
        path.write_text(json.dumps(value))
        manifest=json.loads(self.storage.manifest.read_bytes())
        manifest['jobs'][identifier]['checkpoints'][key]=revision_token(value)
        self.storage.manifest.write_text(json.dumps(manifest))
        before=self.snapshot(identifier)
        with self.assertRaises((ValueError,OSError)):
            await self.project.abackup(self.root/'invalid-backup')
        self.assertEqual(self.snapshot(identifier),before)
        self.assertEqual(self.effects.read_text(),'called\n')
        self.assertFalse((self.root/'invalid-backup').exists())


class GraphReceiptTests(unittest.IsolatedAsyncioTestCase):
    snapshot = checkpoints.CheckpointStorageTests.snapshot

    async def asyncSetUp(self):
        await checkpoints.CheckpointStorageTests.asyncSetUp(self)
        self.graph_effects = self.root / 'graph-effects'
        extract = self.component.extractor.extract
        async def extraction(chunks):
            with self.graph_effects.open('a') as stream:
                stream.write('extracted\n')
            return await extract(chunks)
        self.component.extractor.extract = extraction
        job = await self.rag.aenqueue_document(identifier='manual', title='Manual', content='Alice owns Atlas')
        self.identifier = job['id']
        with patch.object(RAGComponent, 'publish', side_effect=OSError('publication failed')):
            with self.assertRaises(OSError):
                await self.rag.arun_job(self.identifier)
        self.key = 'batches/graph_0.json'
        self.path = self.storage.path(self.identifier) / self.key
        self.original = json.loads(self.path.read_bytes())
        self.original_manifest = self.storage.manifest.read_bytes()
        self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertEqual(self.graph_effects.read_text(), 'extracted\n')

    async def reject(self, value):
        self.path.write_text(json.dumps(value))
        manifest = json.loads(self.original_manifest)
        manifest['jobs'][self.identifier]['checkpoints'][self.key] = revision_token(value)
        self.storage.manifest.write_text(json.dumps(manifest))
        before = self.snapshot(self.identifier)
        for operation in (lambda: self.rag.arun_job(self.identifier, retry=True),
                          lambda: self.project.abackup(self.root / 'invalid-backup'), self.project.aclone):
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(self.snapshot(self.identifier), before)
            self.assertEqual(self.effects.read_text(), 'called\n')
            self.assertEqual(self.graph_effects.read_text(), 'extracted\n')
        self.assertFalse((self.root / 'invalid-backup').exists())
        self.assertEqual(len(await self.app.projects.alist()), 1)

    async def test_missing_stored_relation_metadata_is_not_defaulted_during_resume(self):
        value = deepcopy(self.original)
        del value['value']['relations'][0]['metadata']
        await self.reject(value)

    async def test_current_graph_fields_types_and_canonical_identities_are_required(self):
        cases = []
        for key in self.original['value']:
            value = deepcopy(self.original)
            del value['value'][key]
            cases.append(value)
        for kind in ('entities', 'relations'):
            for key in self.original['value'][kind][0]:
                value = deepcopy(self.original)
                del value['value'][kind][0][key]
                cases.append(value)
        for kind, key, changed in (('entities', 'id', 'changed'), ('entities', 'name', ' Alice '),
                                    ('relations', 'source', 'unknown'), ('relations', 'target', False),
                                    ('relations', 'type', ' owns '), ('relations', 'source_id', None),
                                    ('relations', 'evidence', ''), ('relations', 'metadata', [])):
            value = deepcopy(self.original)
            value['value'][kind][0][key] = changed
            cases.append(value)
        value = deepcopy(self.original)
        value['value']['entities'].append(value['value']['entities'][0])
        cases.append(value)
        cases.append({**deepcopy(self.original), 'signature': 'invalid'})
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                await self.reject(value)

    async def test_normal_retry_reuses_both_real_model_effects(self):
        await self.rag.arun_job(self.identifier, retry=True)
        self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertEqual(self.graph_effects.read_text(), 'extracted\n')
        result = await self.rag.agraph_search('Alice')
        self.assertTrue(result['relations'])
        await self.project.abackup(self.root / 'valid-backup')
