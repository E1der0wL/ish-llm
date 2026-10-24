"""Explicit Job retention must preserve its original input when publication proof is lost."""
from llm.components.rag.corpus_storage import CorpusStorage

from llm.providers.operations import LiteLLMOperations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

from llm.components.rag import RAGComponent
from llm.components.rag.job_storage import JobStorage
from llm.components.rag.jobs import RAGJobs
from llm.services.infrastructure.storage import tree_snapshot
from tests.llm import test_rag_corpus_integrity as fixtures


class RAGJobRetentionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.CorpusContractTests.asyncSetUp

    async def refuse_lost_publication(self, name):
        plan = await self.rag.acheckpoint_plan(self.job['id'])
        cleanup = await self.rag.ajob_cleanup_plan()
        self.assertTrue(plan.can_delete)
        root = self.project.paths.root / 'rag'
        path = self.corpus if name == CorpusStorage.CORPUS_FILE else root / name
        path.unlink()
        before = tree_snapshot(root)
        rejected = False
        try:
            await self.rag.adelete_job(self.job['id'], expected_revision=plan.revision)
        except (ValueError, OSError):
            rejected = True
        self.assertEqual({'rejected': rejected, 'preserved': tree_snapshot(root) == before,
                          'effects': self.effects.read_text()},
                         {'rejected': True, 'preserved': True, 'effects': 'called\n'})
        with self.assertRaises((ValueError, OSError)):
            await self.rag.aprune_jobs([self.job['id']], expected_version=cleanup['version'])
        self.assertEqual(tree_snapshot(root), before)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_job_original_is_preserved_when_active_pointer_is_missing(self):
        await self.refuse_lost_publication(CorpusStorage.ACTIVE_FILE)

    async def test_job_original_is_preserved_when_identity_is_missing(self):
        await self.refuse_lost_publication(CorpusStorage.IDENTITY_FILE)

    async def test_job_original_is_preserved_when_published_corpus_is_missing(self):
        await self.refuse_lost_publication(CorpusStorage.CORPUS_FILE)

    async def second(self):
        job = await self.rag.aenqueue_document(identifier='second', title='Second', content='New independent source')
        await self.rag.arun_job(job['id'])
        return job

    async def test_read_only_json_plan_and_explicit_subset_preserve_other_job_and_corpus(self):
        second = await self.second()
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        effects = self.effects.read_text()
        plan = await self.rag.ajob_cleanup_plan()
        self.assertEqual(json.loads(json.dumps(plan)), plan)
        self.assertEqual({row['job_id'] for row in plan['candidates']}, {self.job['id'], second['id']})
        self.assertEqual(tree_snapshot(root), before)
        original = tree_snapshot(root / 'generations')
        preserved = tree_snapshot(root / 'jobs' / second['id'])
        await self.rag.aprune_jobs([self.job['id']], expected_version=plan['version'])
        self.assertFalse((root / 'jobs' / self.job['id']).exists())
        self.assertEqual(tree_snapshot(root / 'jobs' / second['id']), preserved)
        self.assertEqual(tree_snapshot(root / 'generations'), original)
        self.assertEqual([job['id'] for job in await self.rag.ajobs()], [second['id']])
        self.assertEqual((await self.rag.aget_document(self.identifier))['title'], 'Manual')
        self.assertEqual(self.effects.read_text(), effects)

    async def test_new_job_invalidates_old_plan_and_live_lease_is_protected(self):
        plan = await self.rag.ajob_cleanup_plan()
        await self.rag.aenqueue_document(identifier='pending', title='Pending', content='Unexecuted source')
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            await self.rag.aprune_jobs([self.job['id']], expected_version=plan['version'])
        self.assertEqual(tree_snapshot(root), before)
        lease = RAGJobs(self.rag)._lease(self.job['id'])
        try:
            current = await self.rag.ajob_cleanup_plan()
            self.assertEqual(current['candidates'], [])
            with self.assertRaisesRegex(ValueError, 'protected'):
                await self.rag.aprune_jobs([self.job['id']], expected_version=current['version'])
            self.assertEqual(tree_snapshot(root), before)
        finally:
            lease.close()
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_explicit_targets_cannot_duplicate_or_escape_or_use_missing_version(self):
        plan = await self.rag.ajob_cleanup_plan()
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        for targets, version in (([self.job['id']] * 2, plan['version']), ([self.job['id']], None),
                                 (['../outside'], plan['version']), (['unknown'], plan['version']),
                                 (self.job['id'], plan['version'])):
            with self.subTest(targets=targets, version=version), self.assertRaises(ValueError):
                await self.rag.aprune_jobs(targets, expected_version=version)
            self.assertEqual(tree_snapshot(root), before)
        await self.rag.aprune_jobs([], expected_version=plan['version'])
        self.assertEqual(tree_snapshot(root), before)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_failure_after_first_real_deletion_restores_all_jobs_and_manifest(self):
        second = await self.second()
        plan = await self.rag.ajob_cleanup_plan()
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        effects = self.effects.read_text()
        delete, calls = JobStorage.delete, []
        def failed(storage, identifier):
            delete(storage, identifier)
            calls.append(identifier)
            raise OSError('after Job tree and manifest deletion')
        with patch.object(JobStorage, 'delete', new=failed):
            with self.assertRaisesRegex(OSError, 'tree and manifest'):
                await self.rag.aprune_jobs([self.job['id'], second['id']], expected_version=plan['version'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(tree_snapshot(root), before)
        self.assertEqual(self.effects.read_text(), effects)

    async def test_concurrent_selection_applies_only_once(self):
        plan = await self.rag.ajob_cleanup_plan()
        results = await asyncio.gather(*(self.rag.aprune_jobs([self.job['id']], expected_version=plan['version'])
                                        for _ in range(2)), return_exceptions=True)
        self.assertEqual(sum(value is None for value in results), 1)
        self.assertEqual(sum(isinstance(value, ValueError) for value in results), 1)
        self.assertEqual(await self.rag.ajobs(), [])
        self.assertEqual((await self.rag.aget_document(self.identifier))['title'], 'Manual')
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_cancelled_batch_finishes_all_selected_jobs_and_manifest(self):
        second = await self.second()
        plan = await self.rag.ajob_cleanup_plan()
        root = self.project.paths.root / 'rag'
        corpus, effects = tree_snapshot(root / 'generations'), self.effects.read_text()
        delete = JobStorage.delete
        entered, finish = threading.Event(), threading.Event()
        def paused(storage, identifier):
            delete(storage, identifier)
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('Job deletion was not released')
        with patch.object(JobStorage, 'delete', new=paused):
            task = asyncio.create_task(self.rag.aprune_jobs([self.job['id'], second['id']], expected_version=plan['version']))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                task.cancel()
            finally:
                finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await self.rag.ajobs(), [])
        self.assertFalse((root / 'jobs' / self.job['id']).exists())
        self.assertFalse((root / 'jobs' / second['id']).exists())
        self.assertEqual(tree_snapshot(root / 'generations'), corpus)
        self.assertEqual(self.effects.read_text(), effects)

    async def test_job_deletion_and_restart_do_not_reset_project_quota(self):
        from llm.llm import LargeLanguageModel
        from llm.components.rag import TripleExtractor
        from llm.policies.errors import ExecutionLimitError
        used = (await self.project.amodel_usage())['call_count']
        self.assertGreater(used, 0)
        await self.project.aconfigure_policies({'usage': {'project_max_calls': used}})
        plan = await self.rag.ajob_cleanup_plan()
        await self.rag.aprune_jobs([self.job['id']], expected_version=plan['version'])
        await self.app.shutdown()
        def extraction(**request):
            with self.effects.open('a') as stream:
                stream.write('unexpected extraction\n')
            raise AssertionError('exhausted quota cannot invoke extraction')
        component = RAGComponent(embedding=self.component.embedding,
                                 extractor=TripleExtractor(model='test/extraction', adapter=LiteLLMOperations(call=extraction)))
        async with LargeLanguageModel(self.root / 'workspace', engines={}, components=[component]) as reopened:
            project = await reopened.projects.aload(self.project.id)
            data = project.components.rag
            self.assertEqual((await project.amodel_usage())['call_count'], used)
            job = await data.aenqueue_document(title='Blocked', content='New unmetered source')
            with self.assertRaisesRegex(ExecutionLimitError, 'exhausted'):
                await data.arun_job(job['id'])
            self.assertEqual(self.effects.read_text(), 'called\n')
            self.assertEqual((await project.amodel_usage())['call_count'], used)

    async def test_process_death_keeps_original_or_complete_job_selection(self):
        code = textwrap.dedent('''
            import asyncio, json, os, sys
            from pathlib import Path
            from llm.providers.operations import LiteLLMOperations
            from llm.llm import LargeLanguageModel
            from llm.components.rag import RAGComponent, EmbeddingModel
            from llm.components.rag.job_storage import JobStorage
            from llm.services.infrastructure import transactions
            from llm.services.infrastructure.storage import tree_snapshot
            from tests.llm.configuration_fixtures import rag_project
            from tests.llm.test_rag_components import Extractor, fake_embedding
            root, boundary = Path(sys.argv[1]), sys.argv[2]
            async def embedding(**request):
                with (root / 'effects').open('a') as stream:
                    stream.write('called\\n')
                return await fake_embedding(**request)
            async def main():
                component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', adapter=LiteLLMOperations(call=embedding)), extractor=Extractor())
                async with LargeLanguageModel(root / 'workspace', engines={}, components=[component]) as app:
                    project = await app.projects.acreate(components=['rag'], config=rag_project())
                    data, jobs = project.components.rag, []
                    for index in range(3):
                        job = await data.aenqueue_document(identifier='d' + str(index), title='Document', content='source ' + str(index))
                        await data.arun_job(job['id'])
                        jobs.append(job['id'])
                    plan = await data.ajob_cleanup_plan()
                    rag = project.paths.root / 'rag'
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id, 'jobs': jobs,
                        'before': tree_snapshot(rag), 'usage_count': (await project.amodel_usage())['call_count']}))
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    delete, write = JobStorage.delete, transactions._write
                    count = 0
                    def cut_delete(storage, identifier):
                        nonlocal count
                        delete(storage, identifier)
                        count += 1
                        if boundary == str(count):
                            crash(boundary)
                    def cut_write(path, value):
                        write(path, value)
                        if boundary == 'marker' and path.name == 'COMMITTED':
                            crash('marker')
                    JobStorage.delete, transactions._write = cut_delete, cut_write
                    await data.aprune_jobs(jobs[:2], expected_version=plan['version'])
                    crash('committed')
            asyncio.run(main())
        ''')
        from llm.llm import LargeLanguageModel
        for boundary in ('1', '2', 'marker', 'committed'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                child = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, directory, boundary],
                                                timeout=40, capture_output=True, text=True)
                self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                self.assertEqual((root / 'reached').read_text(), boundary)
                context = json.loads((root / 'context.json').read_text())
                async with LargeLanguageModel(root / 'workspace', engines={}, components=[RAGComponent()]) as app:
                    project = await app.projects.aload(context['project_id'])
                    data = project.components.rag
                    rag = project.paths.root / 'rag'
                    after = tree_snapshot(rag)
                    committed = boundary in ('marker', 'committed')
                    if not committed:
                        self.assertEqual(after, context['before'])
                    else:
                        for identifier in context['jobs'][:2]:
                            self.assertFalse((rag / 'jobs' / identifier).exists())
                        preserved = ('generations/', 'jobs/' + context['jobs'][-1] + '/')
                        self.assertEqual({key: value for key, value in after.items() if key.startswith(preserved)},
                                         {key: value for key, value in context['before'].items() if key.startswith(preserved)})
                    self.assertEqual({job['id'] for job in await data.ajobs()},
                                     set(context['jobs'][-1:] if committed else context['jobs']))
                    self.assertEqual(len(await data.alist_documents()), 3)
                    self.assertEqual((await project.amodel_usage())['call_count'], context['usage_count'])
                    self.assertEqual((root / 'effects').read_text(), 'called\n' * 3)
