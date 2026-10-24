"""Probe remaining RAG state contracts without changing frozen regression sources."""

from llm.providers.operations import LiteLLMOperations
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from llm.llm import LargeLanguageModel
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.rag.jobs import RAGJobs
from llm.components.rag.job_storage import JobStorage
from llm.components.rag.job_records import JobRecord
from llm.services.infrastructure.storage import revision_token
from llm.components.rag import job_storage
from llm.core.contracts import Diagnostic, ResourceRef
from tests.llm.configuration_fixtures import rag_project
from tests.llm.test_rag_components import Extractor, fake_embedding

class JobStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.effects = self.root / 'effects.txt'
        async def embedding(**request):
            with self.effects.open('a') as stream:
                stream.write('called\n')
            return await fake_embedding(**request)
        self.component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', adapter=LiteLLMOperations(call=embedding)), extractor=Extractor())
        self.app = LargeLanguageModel(self.root / 'workspace', engines={}, components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=['rag'], config=rag_project())
        self.rag = self.project.components.rag
        self.job = await self.rag.aenqueue_document(title='Manual', content='backup')
        self.jobs = RAGJobs(self.rag)
        self.storage = JobStorage(await self.project.aget_data(), self.component)
        self.path = self.storage.path(self.job['id'])

    def change(self, name, modify):
        path = self.path / name
        value = json.loads(path.read_bytes())
        modify(value)
        path.write_text(json.dumps(value))
        manifest = json.loads(self.storage.manifest.read_bytes())
        entry = manifest['jobs'][self.job['id']]
        if name == 'job.json':
            entry['revision'] = revision_token(value)
        else:
            entry['checkpoints'][name] = revision_token(value)
        self.storage.manifest.write_text(json.dumps(manifest))

    async def test_lost_running_cancellation_cannot_authorize_a_model_effect(self):
        # Reach the Writer's claimed state before model work, then cancel it.
        await self.rag._async_call(self.jobs._claim, self.job['id'], False)
        await self.rag.acancel_job(self.job['id'])
        self.change('job.json', lambda value: value.pop('cancel_requested'))
        rejected = False
        try:
            await self.rag.arun_job(self.job['id'], retry=True)
        except (ValueError, OSError):
            rejected = True
        self.assertFalse(self.effects.exists(), 'lost cancellation allowed an actual model file effect')
        self.assertTrue(rejected)

    async def test_running_job_requires_its_stored_start_evidence(self):
        await self.rag._async_call(self.jobs._claim, self.job['id'], False)
        self.change('job.json', lambda value: value.pop('started_at'))
        with self.assertRaises(ValueError):
            await self.rag.ajob(self.job['id'])

    async def fail_publication(self):
        with patch.object(RAGComponent, 'publish', side_effect=OSError('publication failed')):
            with self.assertRaises(OSError):
                await self.rag.arun_job(self.job['id'])
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_failed_job_requires_its_stored_diagnostic(self):
        await self.fail_publication()
        self.change('job.json', lambda value: value.pop('diagnostic'))
        with self.assertRaises(ValueError):
            await self.rag.ajob(self.job['id'])

    async def test_lost_abandonment_cannot_reauthorize_an_actual_model_effect(self):
        plan=await self.rag.acheckpoint_plan(self.job['id'])
        await self.rag.aabandon_job(self.job['id'],expected_revision=plan.revision)
        self.change('job.json',lambda value:value.pop('resume_abandonment'))
        rejected=False
        try:
            await self.rag.arun_job(self.job['id'])
        except (ValueError,OSError):
            rejected=True
        self.assertFalse(self.effects.exists(),'lost abandonment authorized an actual model file effect')
        self.assertTrue(rejected)

class JobStateStorageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = JobStateTests.asyncSetUp
    change = JobStateTests.change
    fail_publication = JobStateTests.fail_publication

    async def test_supplied_diagnostic_source_is_preserved_as_observation(self):
        source = ResourceRef('provider', 'explicit-provider')
        class ExternalFailure(RuntimeError):
            diagnostic = Diagnostic('rag_failed', 'upstream unavailable', source=source)
        with patch.object(RAGComponent, 'prepare', side_effect=ExternalFailure('upstream unavailable')):
            with self.assertRaises(ExternalFailure):
                await self.rag.arun_job(self.job['id'])
        diagnostic = await self.rag.ajob_diagnostic(self.job['id'])
        self.assertEqual(diagnostic.source, source)
        self.assertEqual((await self.rag.ajob(self.job['id']))['status'], 'failed')
        self.assertTrue((await self.rag.aresume_plan(self.job['id'])).can_resume)
        self.assertFalse(self.effects.exists())

    def snapshot(self):
        paths = [self.storage.manifest, *self.storage.directory.rglob('*')]
        return {str(path.relative_to(self.storage.manifest.parent)): path.read_bytes()
                for path in paths if path.is_file()}

    def write_record(self, value):
        self.change('job.json', lambda current: (current.clear(), current.update(deepcopy(value))))

    async def reject(self, value):
        self.write_record(value)
        before = self.snapshot()
        effects = self.effects.read_bytes() if self.effects.exists() else None
        for operation in (
                lambda: self.rag.ajob(self.job['id']), self.rag.ajobs,
                lambda: self.rag.ajob_progress(self.job['id']),
                lambda: self.rag.aresume_plan(self.job['id']),
                lambda: self.project.abackup(self.root / 'invalid-backup'), self.project.aclone):
            with self.assertRaises(ValueError):
                await operation()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.effects.read_bytes() if self.effects.exists() else None, effects)
        self.assertFalse((self.root / 'invalid-backup').exists())

    async def test_running_record_requires_every_initial_and_attempt_field(self):
        await self.rag._async_call(self.jobs._claim, self.job['id'], False)
        original = await self.rag.ajob(self.job['id'])
        for key in JobRecord.REQUIRED | JobRecord.ATTEMPT_FIELDS | {'document_id'}:
            with self.subTest(key=key):
                value = deepcopy(original)
                del value[key]
                await self.reject(value)

    async def test_matching_hash_does_not_coerce_state_flags_progress_or_attempt_types(self):
        await self.rag._async_call(self.jobs._claim, self.job['id'], False)
        original = await self.rag.ajob(self.job['id'])
        cases = [('cancel_requested', invalid) for invalid in (None, 0, 'false')]
        cases += [('resume_abandonment', False), ('resume_abandonment', {}),
                  ('resume_abandonment', {'abandoned_at': '2026-01-01T00:00:00+00:00', 'reason': 'explicit'}),
                  ('failures', None), ('failures', {}), ('failures', [{}]),
                  ('progress', None), ('progress', {'completed': True}),
                  ('progress', {'completed': -1}), ('progress', {'total': 1}),
                  ('progress', {'unexpected': 1}), ('ingestion', []),
                  ('pid', True), ('pid', 0), ('configuration', 'different'),
                  ('started_at', '2026-01-01'), ('phase', 'completed'), ('status', 'queued'),
                  ('unexpected', True)]
        for key, replacement in cases:
            with self.subTest(key=key, replacement=replacement):
                value = deepcopy(original)
                value[key] = replacement
                await self.reject(value)

    async def test_failed_record_requires_complete_consistent_failure_evidence(self):
        await self.fail_publication()
        original = await self.rag.ajob(self.job['id'])
        cases = []
        for key in JobRecord.ERROR_FIELDS | {'ended_at'}:
            value = deepcopy(original)
            del value[key]
            cases.append(value)
        for key in original['diagnostic']:
            value = deepcopy(original)
            del value['diagnostic'][key]
            cases.append(value)
        for key in original['diagnostic']['source']:
            value = deepcopy(original)
            del value['diagnostic']['source'][key]
            cases.append(value)
        for change in ({'diagnostic_code': 'different'}, {'error': 'different'},
                       {'ended_at': 'not-time'}, {'failed_phase': ''}):
            value = deepcopy(original)
            value.update(change)
            cases.append(value)
        value = deepcopy(original)
        value['diagnostic']['source']['id'] = ''
        cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                await self.reject(value)

    async def test_retry_preserves_diagnostic_history_without_repeating_model_effect(self):
        await self.fail_publication()
        failure = await self.rag.ajob(self.job['id'])
        result = await self.rag.arun_job(self.job['id'], retry=True)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['failures'], [{key: failure[key] for key in JobRecord.FAILURE_FIELDS}])
        self.assertEqual(self.effects.read_text(), 'called\n')
        original = deepcopy(result)
        for key in JobRecord.FAILURE_FIELDS:
            with self.subTest(key=key):
                value = deepcopy(original)
                del value['failures'][0][key]
                await self.reject(value)
        self.write_record(original)
        value = deepcopy(original)
        del value['ended_at']
        await self.reject(value)

    async def test_unstarted_cancellation_and_explicit_null_abandonment_are_distinct(self):
        self.assertIs(self.job['cancel_requested'], False)
        self.assertIsNone(self.job['resume_abandonment'])
        self.assertEqual(self.job['failures'], [])
        cancelled = await self.rag.acancel_job(self.job['id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertTrue(cancelled['cancel_requested'])
        self.assertFalse(JobRecord.ATTEMPT_FIELDS & cancelled.keys())
        with self.assertRaises(ValueError):
            await self.rag.arun_job(self.job['id'])
        self.assertFalse(self.effects.exists())
        for changes in ({'cancel_requested': False}, {'phase': 'queued'},
                        {'ended_at': '2026-01-01T00:00:00+00:00'}, {'progress': {'completed': 1}}):
            with self.subTest(changes=changes):
                await self.reject({**cancelled, **changes})

    async def test_abandonment_survives_restart_and_owner_cannot_clear_its_receipt(self):
        plan = await self.rag.acheckpoint_plan(self.job['id'])
        abandoned = await self.rag.aabandon_job(self.job['id'], expected_revision=plan.revision)
        before = self.snapshot()
        with self.app.project_manager.ownership.scope():
            with self.assertRaises(ValueError):
                self.storage.save({**abandoned, 'resume_abandonment': None})
        self.assertEqual(self.snapshot(), before)
        await self.app.shutdown()
        async with LargeLanguageModel(self.root / 'workspace', engines={}, components=[self.component]) as reopened:
            project = await reopened.projects.aload(self.project.id)
            with self.assertRaises(ValueError):
                await project.components.rag.arun_job(self.job['id'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.effects.exists())

    async def test_owner_cannot_revoke_running_cancellation(self):
        await self.rag._async_call(self.jobs._claim, self.job['id'], False)
        cancelled = await self.rag.acancel_job(self.job['id'])
        before = self.snapshot()
        with self.app.project_manager.ownership.scope():
            with self.assertRaises(ValueError):
                self.storage.save({**cancelled, 'cancel_requested': False})
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.effects.exists())

    async def test_cancel_and_abandon_write_failures_preserve_record_and_inventory(self):
        before = self.snapshot()
        plan = await self.rag.acheckpoint_plan(self.job['id'])
        operations = (lambda: self.rag.acancel_job(self.job['id']),
                      lambda: self.rag.aabandon_job(self.job['id'], expected_revision=plan.revision))
        original = job_storage.atomic_json
        for operation in operations:
            for target in ('job.json', JobStorage.MANIFEST):
                reached = []
                def fail_after_write(path, value):
                    original(path, value)
                    if Path(path).name == target:
                        reached.append(target)
                        raise OSError('after Job state write')
                with self.subTest(operation=operation, target=target):
                    with patch.object(job_storage, 'atomic_json', side_effect=fail_after_write):
                        with self.assertRaisesRegex(OSError, 'after Job state write'):
                            await operation()
                    self.assertEqual(reached, [target])
                    self.assertEqual(self.snapshot(), before)
                    self.assertFalse(self.effects.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
