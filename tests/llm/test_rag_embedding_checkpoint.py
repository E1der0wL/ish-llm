"""Embedding receipts preserve required payload evidence before reuse and backup."""
import json
from copy import deepcopy
import unittest
from tests.llm import test_rag_checkpoint_integrity as checkpoints
from llm.services.infrastructure.storage import revision_token

class BatchPayloadProbe(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = checkpoints.CheckpointStorageTests.asyncSetUp
    failed_job = checkpoints.CheckpointStorageTests.failed_job
    snapshot = checkpoints.CheckpointStorageTests.snapshot

    async def test_empty_embedding_payload_is_not_a_valid_backup(self):
        identifier = await self.failed_job()
        path = self.storage.path(identifier) / 'batches' / 'embedding_0.json'
        value = json.loads(path.read_bytes())
        value['value'] = {}
        path.write_text(json.dumps(value))
        manifest = json.loads(self.storage.manifest.read_bytes())
        manifest['jobs'][identifier]['checkpoints']['batches/embedding_0.json'] = revision_token(value)
        self.storage.manifest.write_text(json.dumps(manifest))
        before = self.snapshot(identifier)
        with self.assertRaises((ValueError, OSError)):
            await self.project.abackup(self.root / 'invalid-backup')
        self.assertEqual(self.snapshot(identifier), before)
        self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertFalse((self.root / 'invalid-backup').exists())

    async def test_embedding_fields_and_signature_are_checked_before_read_resume_and_backup(self):
        identifier = await self.failed_job()
        path = self.storage.path(identifier) / 'batches' / 'embedding_0.json'
        original = json.loads(path.read_bytes())
        manifest_original = self.storage.manifest.read_bytes()
        cases = []
        for missing in original['value']:
            value = deepcopy(original)
            del value['value'][missing]
            cases.append(value)
        for key, changed in (('fingerprint', False), ('fingerprint', 'invalid'), ('text_hash', ''),
                             ('vector', None), ('vector', []), ('vector', [0, 0]),
                             ('vector', [True, 1]), ('vector', ['bad', 1]), ('extra', True)):
            value = deepcopy(original)
            value['value'][key] = changed
            cases.append(value)
        cases.append({**deepcopy(original), 'signature': 'f' * 64})
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                path.write_text(json.dumps(value))
                manifest = json.loads(manifest_original)
                manifest['jobs'][identifier]['checkpoints']['batches/embedding_0.json'] = revision_token(value)
                self.storage.manifest.write_text(json.dumps(manifest))
                before = self.snapshot(identifier)
                for operation in (lambda: self.rag.arun_job(identifier, retry=True),
                                  lambda: self.project.abackup(self.root / 'invalid-backup'), self.project.aclone):
                    with self.assertRaises((ValueError, OSError)):
                        await operation()
                    self.assertEqual(self.snapshot(identifier), before)
                    self.assertEqual(self.effects.read_text(), 'called\n')
                with self.assertRaises(ValueError):
                    self.storage.checkpoints(identifier)
                self.assertFalse((self.root / 'invalid-backup').exists())
                self.assertEqual(len(await self.app.projects.alist()), 1)
                path.write_text(json.dumps(original))
                self.storage.manifest.write_bytes(manifest_original)

    async def test_normal_retry_reuses_the_original_chunk_effect(self):
        identifier = await self.failed_job()
        await self.rag.arun_job(identifier, retry=True)
        self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertEqual((await self.rag.aget_document('manual'))['title'], 'Manual')
        await self.project.abackup(self.root / 'valid-backup')
