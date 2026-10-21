"""Only an explicit boolean retry command can claim a Job or repeat a model call."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from llm.components.rag import RAGComponent, EmbeddingModel
from llm.llm import LargeLanguageModel, BackendServices
from llm.providers.requests import ProviderError
from llm.services.infrastructure.logging import DomainLogger, FileLogSink
from tests.llm.configuration_fixtures import rag_project


class RAGRetryPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.effects = self.root / 'model-effects'
        self.fail_next = False

        async def embedding(**request):
            with self.effects.open('a') as stream:
                stream.write('embedding\n')
            if self.fail_next:
                self.fail_next = False
                raise ConnectionError('Model response was lost')
            return SimpleNamespace(data=[{'index': index, 'embedding': [1.0, 2.0, 3.0]}
                for index, _ in enumerate(request['input'])])

        # Read diagnostics remain enabled outside the authoritative Project files.
        log_sink = FileLogSink()
        logger = DomainLogger(sink=lambda logs, event, fields:
            log_sink(self.root / 'diagnostics', event, fields))
        self.app = await self.enterAsyncContext(LargeLanguageModel(self.root / 'workspace',
            components=[RAGComponent(embedding=EmbeddingModel(model='test', embedding_fn=embedding))],
            engines={}, services=BackendServices(logger=logger)))
        self.project = await self.app.projects.acreate(components=['rag'], config=rag_project({
            'parameters': {'components': {'rag': {'policy': {
                'extraction': {'failure_policy': 'disabled'}, 'provider': {'max_attempts': 1}}}}}}))
        self.rag = await self.project.components.aget('rag')
        self.project_root = (await self.project.aget_data()).paths.root

    def effect_text(self):
        return self.effects.read_text() if self.effects.exists() else ''

    def project_files(self):
        return {str(path.relative_to(self.project_root)): path.read_bytes()
            for path in self.project_root.rglob('*') if path.is_file()}

    async def enqueue(self, identifier):
        return await self.rag.aenqueue_document(title=identifier, content=f'Unique document {identifier}.')

    async def test_invalid_flags_preserve_queued_job_and_all_project_files(self):
        for index, value in enumerate((0, 1, None, 'false', [], {}, 1.0, object())):
            with self.subTest(value=repr(value)):
                job = await self.enqueue(f'queued-{index}')
                before, effects = self.project_files(), self.effect_text()
                error = None
                try:
                    await self.rag.arun_job(job['id'], retry=value)
                except Exception as caught:
                    error = caught
                self.assertEqual(self.effect_text(), effects, 'Invalid retry flag caused a model effect')
                self.assertIsInstance(error, TypeError)
                self.assertEqual(self.project_files(), before)
                self.assertEqual((await self.rag.ajob(job['id']))['status'], 'queued')

    async def test_invalid_truthy_flag_cannot_retry_failed_job_but_true_can(self):
        job = await self.enqueue('failed')
        self.fail_next = True
        with self.assertRaises(ProviderError):
            await self.rag.arun_job(job['id'])
        self.assertEqual((await self.rag.ajob(job['id']))['status'], 'failed')
        before = self.project_files()
        self.assertEqual(self.effect_text(), 'embedding\n')
        error = None
        try:
            await self.rag.arun_job(job['id'], retry='false')
        except Exception as caught:
            error = caught
        self.assertEqual(self.effect_text(), 'embedding\n', 'Invalid flag repeated a failed model call')
        self.assertIsInstance(error, TypeError)
        self.assertEqual(self.project_files(), before)
        plan = await self.rag.aresume_plan(job['id'])
        await self.rag.arun_job(job['id'], retry=True, expected_revision=plan.revision)
        self.assertEqual((await self.rag.ajob(job['id']))['status'], 'completed')
        self.assertEqual(self.effect_text(), 'embedding\n' * 2)

    async def test_omitted_and_false_flags_run_queued_jobs_without_retry_permission(self):
        first = await self.enqueue('omitted')
        second = await self.enqueue('explicit-false')
        await self.rag.arun_job(first['id'])
        await self.rag.arun_job(second['id'], retry=False)
        self.assertEqual(self.effect_text(), 'embedding\n' * 2)
        self.assertEqual((await self.rag.ajob(first['id']))['status'], 'completed')
        self.assertEqual((await self.rag.ajob(second['id']))['status'], 'completed')
        failed = await self.enqueue('no-retry')
        self.fail_next = True
        with self.assertRaises(ProviderError):
            await self.rag.arun_job(failed['id'])
        before, effects = self.project_files(), self.effect_text()
        with self.assertRaises(ValueError):
            await self.rag.arun_job(failed['id'], retry=False)
        self.assertEqual(self.effect_text(), effects)
        self.assertEqual(self.project_files(), before)
