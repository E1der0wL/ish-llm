"""Stored model receipts remain bound to the actual original chunk request."""

from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from llm.services.infrastructure.storage import revision_token
from llm.components.rag import RAGComponent
from llm.core.models import ProjectConfig
from tests.llm import test_rag_checkpoint_integrity as checkpoints


class BatchSourceTests(unittest.IsolatedAsyncioTestCase):
    failed_job = checkpoints.CheckpointStorageTests.failed_job
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

    async def failed_graph(self):
        job = await self.rag.aenqueue_document(title='Manual', content='Alice owns Atlas', identifier='manual')
        with patch.object(RAGComponent, 'publish', side_effect=OSError('publication failed')):
            with self.assertRaises(OSError):
                await self.rag.arun_job(job['id'])
        return job['id']

    def replace_receipt(self, identifier, key, receipt):
        self.storage.checkpoint_path(identifier, key).write_text(json.dumps(receipt))
        manifest = json.loads(self.storage.manifest.read_bytes())
        manifest['jobs'][identifier]['checkpoints'][key] = revision_token(receipt)
        self.storage.manifest.write_text(json.dumps(manifest))

    async def test_graph_quotation_outside_original_input_cannot_be_backed_up(self):
        identifier = await self.failed_graph()
        key = 'batches/graph_0.json'
        receipt = json.loads(self.storage.checkpoint_path(identifier, key).read_bytes())
        receipt['value']['graph']['relations'][0]['evidence'] = 'Bob owns Harbor'
        self.replace_receipt(identifier, key, receipt)
        before = self.snapshot(identifier)
        with self.assertRaises(ValueError):
            await self.project.abackup(self.root / 'backup')
        self.assertFalse((self.root / 'backup').exists())
        self.assertEqual(self.snapshot(identifier), before)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def reject_receipt(self, identifier, key, receipt):
        self.replace_receipt(identifier, key, receipt)
        before = self.snapshot(identifier)
        plan = await self.rag.aresume_plan(identifier)
        self.assertEqual(plan.state, 'corrupt')
        self.assertFalse(plan.can_resume)
        for operation in (lambda: self.rag.arun_job(identifier, retry=True),
                          lambda: self.project.abackup(self.root / 'backup'), self.project.aclone):
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(self.snapshot(identifier), before)
            self.assertEqual(self.effects.read_text(), 'called\n')
            self.assertEqual(self.graph_effects.read_text(), 'extracted\n')
        self.assertFalse((self.root / 'backup').exists())
        self.assertEqual(len(await self.app.projects.alist()), 1)

    async def test_graph_source_fields_and_chunk_identity_cannot_be_reconstructed(self):
        identifier = await self.failed_graph()
        key = 'batches/graph_0.json'
        original = self.storage.read_checkpoint(identifier, key)
        cases = []
        for field in original['value']:
            value = deepcopy(original)
            del value['value'][field]
            cases.append(value)
        for field, replacement in (('chunk_size', None), ('chunk_size', True), ('chunk_size', 0),
                                   ('chunks', []), ('chunks', {})):
            value = deepcopy(original)
            value['value'][field] = replacement
            cases.append(value)
        for field in original['value']['chunks'][0]:
            value = deepcopy(original)
            del value['value']['chunks'][0][field]
            cases.append(value)
        value = deepcopy(original)
        value['value']['chunks'][0]['text'] = 'Bob owns Harbor'
        value['value']['graph']['relations'][0]['evidence'] = 'Bob owns Harbor'
        cases.append(value)
        value = deepcopy(original)
        value['value']['chunks'][0]['id'] = 'manual:c99'
        value['value']['graph']['relations'][0]['source_id'] = 'manual:c99'
        cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                await self.reject_receipt(identifier, key, value)

    async def test_embedding_valid_hash_still_requires_the_exact_original_chunk(self):
        identifier = await self.failed_job()
        key = 'batches/embedding_0.json'
        original = self.storage.read_checkpoint(identifier, key)
        for field, replacement in (('chunk_size', True), ('chunk_size', 1), ('chunk', {})):
            value = deepcopy(original)
            value['value'][field] = replacement
            await self.reject_receipt(identifier, key, value)
        for field, replacement in (('text', 'invented'), ('id', 'manual:c99'),
                                   ('document_id', 'other'), ('heading', 'invented'), ('section_id', 'manual:s99')):
            value = deepcopy(original)
            value['value']['chunk'][field] = replacement
            value['value']['text_hash'] = sha256(value['value']['chunk']['text'].encode()).hexdigest()
            value['signature'] = revision_token({name: value['value'][name] for name in ('fingerprint', 'text_hash')})
            await self.reject_receipt(identifier, key, value)

    async def test_stored_chunk_proof_does_not_use_changed_live_configuration(self):
        identifier = await self.failed_graph()
        before = self.snapshot(identifier)
        current = (await self.project.aget_data()).config.parameters['components']['rag']
        await self.rag.aconfigure(ProjectConfig.merge(current, {'config': {'chunk_size': 1}}))
        await self.project.abackup(self.root / 'backup')
        self.assertEqual(self.snapshot(identifier), before)
        plan = await self.rag.aresume_plan(identifier)
        self.assertEqual(plan.state, 'incompatible')
        self.assertFalse(plan.can_resume)
        self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertEqual(self.graph_effects.read_text(), 'extracted\n')

    async def test_embedding_hash_unrelated_to_original_chunk_cannot_be_backed_up(self):
        identifier = await self.failed_job()
        key = 'batches/embedding_0.json'
        receipt = json.loads(self.storage.checkpoint_path(identifier, key).read_bytes())
        receipt['value']['text_hash'] = '0' * 64
        receipt['signature'] = revision_token({name: receipt['value'][name] for name in ('fingerprint', 'text_hash')})
        self.replace_receipt(identifier, key, receipt)
        before = self.snapshot(identifier)
        with self.assertRaises(ValueError):
            await self.project.abackup(self.root / 'backup')
        self.assertFalse((self.root / 'backup').exists())
        self.assertEqual(self.snapshot(identifier), before)
        self.assertEqual(self.effects.read_text(), 'called\n')
