"""A stored batch name must stay bound to its completed model effect."""

from llm.providers.operations import LiteLLMOperations
import json
import hashlib
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from llm.llm import LargeLanguageModel
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.rag.jobs import RAGJobs
from tests.llm.configuration_fixtures import rag_project
from tests.llm.test_rag_components import fake_embedding
from tests.llm import test_rag_checkpoint_integrity as checkpoint_tests
from llm.services.infrastructure.storage import revision_token

class RenamedBatchProbe(unittest.IsolatedAsyncioTestCase):
    async def test_renamed_chunk_receipt_cannot_repeat_the_completed_first_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);effects=root/'effects';failing=True
            async def embedding(**request):
                text=request['input'][0]
                if text=='second' and failing:raise ConnectionError('injected second chunk failure')
                with effects.open('a') as stream:stream.write(text+'\n')
                return await fake_embedding(**request)
            component=RAGComponent(embedding=EmbeddingModel(model='test/embedding',adapter=LiteLLMOperations(call=embedding)))
            async with LargeLanguageModel(root/'workspace',engines={},components=[component]) as app:
                settings = {'config': {'embedding_cache_max_bytes': 0}, 'policy': {'embedding_concurrency': 1, 'extraction': {'failure_policy': 'disabled'}}}
                project = await app.projects.acreate(components=['rag'], config=rag_project({'parameters': {'components': {'rag': settings}}}))
                rag=project.components.rag
                job=await rag.aenqueue_document(identifier='manual',title='Manual',content='first\n\nsecond')
                with self.assertRaises(Exception):await rag.arun_job(job['id'])
                self.assertEqual(effects.read_text(),'first\n')
                owner=RAGJobs(rag)._storage();key='batches/embedding_0.json';renamed='batches/embedding_99.json'
                (owner.path(job['id'])/key).rename(owner.path(job['id'])/renamed)
                manifest=json.loads(owner.manifest.read_bytes());values=manifest['jobs'][job['id']]['checkpoints'];values[renamed]=values.pop(key);owner.manifest.write_text(json.dumps(manifest))
                def snapshot():
                    source=project.paths.root/'rag'
                    return {str(path.relative_to(source)):hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in source.rglob('*') if path.is_file()}
                before=snapshot()
                failing=False;rejected=False
                try:await rag.arun_job(job['id'],retry=True)
                except (ValueError,OSError):rejected=True
                self.assertEqual(effects.read_text(),'first\n','renamed receipt repeated a completed first chunk effect')
                self.assertTrue(rejected)
                self.assertEqual(snapshot(),before)
                with self.assertRaises((ValueError,OSError)):
                    await project.abackup(root/'invalid-backup')
                self.assertEqual(snapshot(),before)
                self.assertFalse((root/'invalid-backup').exists())


class BatchIdentityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = checkpoint_tests.CheckpointStorageTests.asyncSetUp
    failed_job = checkpoint_tests.CheckpointStorageTests.failed_job
    snapshot = checkpoint_tests.CheckpointStorageTests.snapshot

    async def test_every_batch_receipt_requires_its_exact_stored_key_even_with_matching_hash(self):
        identifier = await self.failed_job()
        originals = self.snapshot(identifier)
        manifest = json.loads(self.storage.manifest.read_bytes())
        for key, receipt in self.storage.checkpoints(identifier)[1].items():
            if not key.startswith('batches/'):
                continue
            self.assertEqual(receipt['key'], key)
            for replacement in (None, False, [], 'batches/other.json', 'batches/embedding_99.json'):
                if replacement == key:
                    continue
                with self.subTest(key=key, replacement=replacement):
                    changed = deepcopy(receipt)
                    if replacement is None:
                        del changed['key']
                    else:
                        changed['key'] = replacement
                    self.storage.checkpoint_path(identifier, key).write_text(json.dumps(changed))
                    damaged = deepcopy(manifest)
                    damaged['jobs'][identifier]['checkpoints'][key] = revision_token(changed)
                    self.storage.manifest.write_text(json.dumps(damaged))
                    before = self.snapshot(identifier)
                    for operation in (lambda: self.rag.arun_job(identifier, retry=True),
                                      lambda: self.project.abackup(self.root/'invalid-backup'), self.project.aclone):
                        with self.assertRaises((ValueError, OSError)):
                            await operation()
                        self.assertEqual(self.snapshot(identifier), before)
                        self.assertEqual(self.effects.read_text(), 'called\n')
                    self.assertFalse((self.root/'invalid-backup').exists())
                    self.assertEqual(len(await self.app.projects.alist()), 1)
                    for name, content in originals.items():
                        (self.storage.manifest.parent/name).write_bytes(content)

    async def test_writer_refuses_mismatched_key_before_any_file_change(self):
        identifier = await self.failed_job()
        key = 'batches/embedding_0.json'
        receipt = self.storage.read_checkpoint(identifier, key)
        before = self.snapshot(identifier)
        with self.app.project_manager.ownership.scope(), self.assertRaises(ValueError):
            self.storage.write_checkpoint(identifier, key, {**receipt, 'key': 'batches/embedding_99.json'})
        self.assertEqual(self.snapshot(identifier), before)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_normal_retry_reuses_bound_receipts(self):
        identifier = await self.failed_job()
        await self.rag.arun_job(identifier, retry=True)
        self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertEqual((await self.rag.aget_document('manual'))['title'], 'Manual')
        await self.project.abackup(self.root/'valid-backup')
