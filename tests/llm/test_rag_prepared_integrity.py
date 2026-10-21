"""Prepared RAG evidence must be meaningful before reuse or backup."""
import unittest
import json
from copy import deepcopy
from hashlib import sha256
from unittest.mock import patch
from llm.components.rag import RAGComponent
from llm.components.rag.extraction import GraphValidationError
from tests.llm.configuration_fixtures import rag_settings
from tests.llm import test_rag_job_state_integrity as state


class PreparedContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = state.JobStateTests.asyncSetUp
    change = state.JobStateTests.change
    fail_publication = state.JobStateTests.fail_publication
    snapshot = state.JobStateStorageTests.snapshot

    def replace(self, value, name='prepared.json'):
        self.change(name, lambda current: (current.clear(), current.update(deepcopy(value))))

    async def reject(self, value, name='prepared.json'):
        self.replace(value, name)
        before, effects = self.snapshot(), self.effects.read_bytes()
        plan = await self.rag.aresume_plan(self.job['id'])
        self.assertEqual(plan.state, 'corrupt')
        self.assertFalse(plan.can_resume)
        for operation in (lambda: self.rag.arun_job(self.job['id'], retry=True),
                          lambda: self.project.abackup(self.root / 'backup'), self.project.aclone):
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.effects.read_bytes(), effects)
        self.assertFalse((self.root / 'backup').exists())
        self.assertEqual(len(await self.app.projects.alist()), 1)

    async def prepared(self):
        await self.fail_publication()
        return json.loads((self.path / 'prepared.json').read_bytes())

    async def test_empty_prepared_receipt_is_not_a_valid_backup(self):
        await self.fail_publication()
        self.change('prepared.json', lambda value: value.clear())
        with self.assertRaises(ValueError):
            await self.project.abackup(self.root / 'backup')
        self.assertFalse((self.root / 'backup').exists())

    async def test_every_stored_document_field_is_required_even_with_a_matching_hash(self):
        original = await self.prepared()
        for key in RAGComponent.PREPARED_FIELDS:
            with self.subTest(key=key):
                value = deepcopy(original)
                del value[key]
                await self.reject(value)

    async def test_nested_source_profile_graph_and_manifest_corruption_is_not_repaired(self):
        original = await self.prepared()
        cases = []
        for key, replacement in (
                ('sections', {}), ('chunks', []), ('sha256', '0' * 64), ('metadata', []),
                ('revision', True), ('generation', 'wrong'), ('ingestion', []),
                ('embedding_fingerprint', None), ('preparation_fingerprint', ''),
                ('vectors', []), ('vectors', [[0, 0, 0]]), ('profile', {}),
                ('graph', {}), ('graph_complete', 1), ('graph_diagnostics', [{}]),
                ('extraction_manifest', []), ('extraction_manifest', [{}]), ('description', None)):
            value = deepcopy(original)
            value[key] = replacement
            cases.append(value)
        for key, replacement in (('document_id', 'other'), ('id', 'other:c1'),
                                 ('section_id', 'missing'), ('heading', 'other'), ('text', 'fabricated')):
            value = deepcopy(original)
            value['chunks'][0][key] = replacement
            cases.append(value)
        for key in original['chunks'][0]:
            value = deepcopy(original)
            del value['chunks'][0][key]
            cases.append(value)
        for key in next(iter(original['sections'].values())):
            value = deepcopy(original)
            del next(iter(value['sections'].values()))[key]
            cases.append(value)
        for key, replacement in (('offset', True), ('count', 0), ('digest', '0' * 64),
                                 ('entity_ids', ['missing']), ('signature', 'changed')):
            value = deepcopy(original)
            value['extraction_manifest'][0][key] = replacement
            cases.append(value)
        for position, value in enumerate(cases):
            with self.subTest(case=position):
                await self.reject(value)

    async def test_valid_document_shape_still_requires_original_input_and_attempt(self):
        original = await self.prepared()
        for key, replacement in (('title', 'Different'), ('metadata', {'new': True}),
                                 ('revision', 2), ('description', 'Unrequested'),
                                 ('preparation_configuration', None),
                                 ('preparation_configuration', '0' * 64)):
            with self.subTest(key=key, replacement=replacement):
                value = deepcopy(original)
                value[key] = replacement
                RAGComponent.validate_prepared_document(value)
                await self.reject(value)
        value = deepcopy(original)
        value['content'] += '\nUnrequested new source.'
        value['sha256'] = sha256(value['content'].encode()).hexdigest()
        RAGComponent.validate_prepared_document(value)
        await self.reject(value)

    async def test_boolean_cannot_replace_original_numeric_metadata(self):
        self.job = await self.rag.aenqueue_document(title='Metadata', content='backup', metadata={'count': 1})
        self.path = self.storage.path(self.job['id'])
        await self.fail_publication()
        self.change('prepared.json', lambda value: value['metadata'].update(count=True))
        with self.assertRaises(ValueError):
            await self.project.abackup(self.root / 'backup')
        self.assertFalse((self.root / 'backup').exists())
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_nested_json_types_cannot_change_even_when_python_values_compare_equal(self):
        metadata = {'count': 1, 'enabled': True, 'scores': [1.0, 0]}
        self.job = await self.rag.aenqueue_document(title='Metadata', content='backup', metadata=metadata)
        self.path = self.storage.path(self.job['id'])
        original = await self.prepared()
        for changes in ({'count': True}, {'enabled': 1}, {'scores': [1, 0]}, {'scores': [1.0, False]}):
            with self.subTest(changes=changes):
                value = deepcopy(original)
                value['metadata'].update(changes)
                self.assertEqual(value['metadata'], metadata, 'exercise the Python equality ambiguity')
                await self.reject(value)
        self.replace(original)
        self.assertEqual((await self.rag.arun_job(self.job['id'], retry=True))['status'], 'completed')
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_missing_input_cannot_be_replaced_by_a_prepared_copy(self):
        await self.prepared()
        (self.path / 'input.json').unlink()
        before = self.snapshot()
        plan = await self.rag.aresume_plan(self.job['id'])
        self.assertEqual(plan.state, 'missing')
        self.assertFalse(plan.can_resume)
        for operation in (lambda: self.rag.arun_job(self.job['id'], retry=True),
                          lambda: self.project.abackup(self.root / 'backup'), self.project.aclone):
            with self.assertRaises((ValueError, OSError)):
                await operation()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_stored_graph_cannot_default_or_rewrite_its_source_evidence(self):
        self.job = await self.rag.aenqueue_document(title='Graph', content='Alice owns Atlas')
        self.path = self.storage.path(self.job['id'])
        original = await self.prepared()
        self.assertTrue(original['graph']['relations'])
        cases = []
        for key in original['graph']['relations'][0]:
            value = deepcopy(original)
            del value['graph']['relations'][0][key]
            cases.append(value)
        for key, replacement in (('document_id', 'other'), ('extracted_at', '2026-01-01'),
                                 ('weight', True), ('weight', 2)):
            value = deepcopy(original)
            value['graph']['relations'][0][key] = replacement
            cases.append(value)
        value = deepcopy(original)
        value['graph']['relations'] *= 2
        cases.append(value)
        for position, value in enumerate(cases):
            with self.subTest(case=position):
                await self.reject(value)
        self.replace(original)
        self.assertEqual((await self.rag.arun_job(self.job['id'], retry=True))['status'], 'completed')
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_owner_rejects_invalid_replacement_before_writing_and_valid_retry_reuses_effect(self):
        original = await self.prepared()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            await self.rag._async_call(self.jobs._prepared, self.job['id'], {})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertEqual(RAGComponent.validate_prepared_document(original), original)
        await self.project.abackup(self.root / 'backup')
        self.assertTrue((self.root / 'backup').exists())
        self.assertEqual((await self.rag.arun_job(self.job['id'], retry=True))['status'], 'completed')
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_disabled_and_incomplete_graph_receipts_preserve_diagnostics_without_new_effects(self):
        for policy in ('disabled', 'best_effort'):
            with self.subTest(policy=policy):
                await self.rag.aconfigure(rag_settings({'policy': {'extraction': {'failure_policy': policy}}}))
                self.job = await self.rag.aenqueue_document(title='Manual', content='backup')
                self.path = self.storage.path(self.job['id'])
                with patch.object(state.Extractor, 'extract', side_effect=GraphValidationError('unavailable')):
                    with patch.object(RAGComponent, 'publish', side_effect=OSError('publication failed')):
                        with self.assertRaises(OSError):
                            await self.rag.arun_job(self.job['id'])
                value = json.loads((self.path / 'prepared.json').read_bytes())
                self.assertFalse(value['graph_complete'])
                self.assertTrue(value['graph_diagnostics'])
                self.assertIsNone(value['graph_diagnostics'][0]['source'])
                RAGComponent.validate_prepared_document(value)
                effects = self.effects.read_bytes()
                self.assertTrue((await self.rag.aresume_plan(self.job['id'])).can_resume)
                await self.rag.arun_job(self.job['id'], retry=True)
                self.assertEqual(self.effects.read_bytes(), effects)

    async def test_batch_envelope_order_and_cached_document_are_bound_to_original_inputs(self):
        self.job = await self.rag.aenqueue_documents([
            {'identifier': 'one', 'title': 'One', 'content': 'backup one', 'description': None},
            {'identifier': 'two', 'title': 'Two', 'content': 'backup two', 'description': 'Two guide'}])
        self.path = self.storage.path(self.job['id'])
        with patch.object(RAGComponent, 'publish', side_effect=OSError('publication failed')):
            with self.assertRaises(OSError):
                await self.rag.arun_job(self.job['id'])
        original = json.loads((self.path / 'prepared.json').read_bytes())
        cases = [{}, {'documents': []}, {'documents': original['documents'][:1]},
                 {'documents': original['documents'][::-1]}, {**original, 'unexpected': True}]
        for value in cases:
            await self.reject(value)
        self.replace(original)
        name = 'batches/document_0.json'
        cached = json.loads((self.path / name).read_bytes())
        for value in ({**cached, 'value': {}}, {**cached, 'signature': 'changed'},
                      {**cached, 'value': original['documents'][1]}):
            await self.reject(value, name)
        self.replace(cached, name)
        effects = self.effects.read_bytes()
        self.assertTrue((await self.rag.aresume_plan(self.job['id'])).can_resume)
        self.assertEqual((await self.rag.arun_job(self.job['id'], retry=True))['status'], 'completed')
        self.assertEqual(self.effects.read_bytes(), effects)


if __name__ == '__main__':
    unittest.main(verbosity=2)
