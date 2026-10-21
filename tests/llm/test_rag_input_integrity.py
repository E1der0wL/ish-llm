"""Queued original inputs cannot acquire creation defaults after storage."""

from copy import deepcopy
import json
import unittest

from llm.services.infrastructure.storage import revision_token
from llm.components.rag import RAGComponent
from tests.llm import test_rag_job_state_integrity as state


class InputIntegrityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = state.JobStateTests.asyncSetUp
    snapshot = state.JobStateStorageTests.snapshot

    def replace_input(self, payload):
        (self.path / 'input.json').write_text(json.dumps(payload))
        job = json.loads((self.path / 'job.json').read_bytes())
        job['input_digest'] = revision_token(payload)
        (self.path / 'job.json').write_text(json.dumps(job))
        manifest = json.loads(self.storage.manifest.read_bytes())
        manifest['jobs'][self.job['id']]['revision'] = revision_token(job)
        self.storage.manifest.write_text(json.dumps(manifest))

    async def test_missing_queued_metadata_is_rejected_before_claim(self):
        payload = json.loads((self.path / 'input.json').read_bytes())
        del payload['metadata']
        self.replace_input(payload)
        before = self.snapshot()
        rejected = False
        try:
            await self.rag.arun_job(self.job['id'])
        except (ValueError, OSError):
            rejected = True
        self.assertFalse(self.effects.exists(), 'missing original metadata allowed a model file effect')
        self.assertTrue(rejected)
        self.assertEqual(self.snapshot(), before)

    async def test_missing_queued_metadata_cannot_be_backed_up(self):
        payload = json.loads((self.path / 'input.json').read_bytes())
        del payload['metadata']
        self.replace_input(payload)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            await self.project.abackup(self.root / 'backup')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / 'backup').exists())

    async def test_single_enqueue_does_not_coerce_false_metadata(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            await self.rag.aenqueue_document(title='Manual', content='backup', metadata=False)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.effects.exists())

    async def reject_input(self, payload):
        self.replace_input(payload)
        before = self.snapshot()
        plan = await self.rag.aresume_plan(self.job['id'])
        self.assertEqual(plan.state, 'corrupt')
        self.assertFalse(plan.can_resume)
        for operation in (lambda: self.rag.arun_job(self.job['id']),
                          lambda: self.project.abackup(self.root / 'backup'), self.project.aclone):
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(self.snapshot(), before)
            self.assertFalse(self.effects.exists())
        self.assertFalse((self.root / 'backup').exists())
        self.assertEqual(len(await self.app.projects.alist()), 1)

    async def test_stored_fields_types_and_identity_are_required_even_with_matching_hashes(self):
        original = json.loads((self.path / 'input.json').read_bytes())
        cases = []
        for key in RAGComponent.INPUT_FIELDS:
            value = deepcopy(original)
            del value[key]
            cases.append(value)
        for key, replacement in (
                ('identifier', None), ('identifier', False), ('identifier', 'different'),
                ('title', None), ('title', []), ('title', ' '),
                ('content', False), ('content', ''), ('metadata', None),
                ('metadata', False), ('metadata', []), ('description', False), ('extra', True)):
            cases.append({**original, key: replacement})
        for position, value in enumerate(cases):
            with self.subTest(case=position):
                await self.reject_input(value)

    async def test_batch_originals_require_the_exact_declared_order_and_count(self):
        self.job = await self.rag.aenqueue_documents([
            {'identifier': 'one', 'title': 'One', 'content': 'first'},
            {'identifier': 'two', 'title': 'Two', 'content': 'second'}])
        self.path = self.storage.path(self.job['id'])
        original = json.loads((self.path / 'input.json').read_bytes())
        inputs = original['documents']
        cases = [{}, {'documents': None}, {'documents': {}}, {'documents': []},
                 {'documents': inputs[:1]}, {'documents': inputs[::-1]},
                 {'documents': [inputs[0], inputs[0]]}, {**original, 'extra': True}]
        for key in RAGComponent.INPUT_FIELDS:
            value = deepcopy(original)
            del value['documents'][1][key]
            cases.append(value)
        for position, value in enumerate(cases):
            with self.subTest(case=position):
                await self.reject_input(value)

    async def test_all_creation_paths_reject_wrong_types_without_admission_or_model_effects(self):
        base = {'title': 'Manual', 'content': 'backup'}
        before = self.snapshot()
        for key, replacement in (
                ('identifier', False), ('identifier', 0), ('identifier', ''),
                ('title', None), ('title', ' '), ('content', []), ('content', ''),
                ('metadata', False), ('metadata', 0), ('metadata', []),
                ('metadata', {'value': float('nan')}), ('description', False)):
            with self.subTest(key=key, value=replacement):
                source = {**base, key: replacement}
                for operation in (lambda: self.rag.aenqueue_document(**source),
                                  lambda: self.rag.aenqueue_documents([source]),
                                  lambda: self.rag.aadd_documents([source])):
                    with self.assertRaises(ValueError):
                        await operation()
                    self.assertEqual(self.snapshot(), before)
                    self.assertFalse(self.effects.exists())

    async def test_storage_writer_rejects_invalid_input_before_creating_a_job(self):
        payload = json.loads((self.path / 'input.json').read_bytes())
        del payload['metadata']
        job = {**self.job, 'id': 'otherjob', 'input_digest': revision_token(payload)}
        before = self.snapshot()
        with self.app.project_manager.ownership.scope(), self.assertRaises(ValueError):
            self.storage.create(job, payload)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.storage.path(job['id']).exists())

    async def test_creation_defaults_and_nullable_description_remain_valid(self):
        job = await self.rag.aenqueue_document(title='Single', content='first', metadata=None)
        self.assertEqual(job['id'], job['document_id'])
        payload = self.storage.read_input(job['id'], job)
        self.assertEqual(payload['metadata'], {})
        self.assertNotIn('description', payload)
        batch = await self.rag.aenqueue_documents([
            {'title': 'Batch', 'content': 'second', 'identifier': None, 'metadata': None, 'description': None}])
        payload = self.storage.read_input(batch['id'], batch)
        self.assertEqual(payload['documents'][0]['metadata'], {})
        self.assertIsNone(payload['documents'][0]['description'])
        await self.project.abackup(self.root / 'backup')
        for record in (job, batch):
            self.assertEqual((await self.rag.arun_job(record['id']))['status'], 'completed')
        self.assertEqual(self.effects.read_text(), 'called\ncalled\n')
