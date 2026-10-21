"""RAG Job record and lease loss must not erase work or authorize another effect."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.rag.jobs import RAGJobs
from llm.components.rag.job_storage import JobStorage
from llm.services.infrastructure.storage import revision_token
from tests.llm.configuration_fixtures import rag_project
from tests.llm.test_rag_components import Extractor, fake_embedding


class JobLossTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.effects = self.root / 'model-effects.txt'
        self.calls = 0

        async def embedding(**request):
            self.calls += 1
            with self.effects.open('a') as stream:
                stream.write('called\n')
            if self.calls == 1:
                self.entered.set()
                await self.release.wait()
            return await fake_embedding(**request)

        component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', embedding_fn=embedding),
                                 extractor=Extractor())
        self.component = component
        self.app = LargeLanguageModel(self.root / 'workspace', engines={}, components=[component])
        self.addAsyncCleanup(self.app.shutdown)
        project = await self.app.projects.acreate('probe', components=['rag'], config=rag_project())
        self.project = project
        self.rag = await project.components.aget('rag')

    async def storage(self):
        return JobStorage(await self.project.aget_data(), self.component)

    @staticmethod
    def snapshot(storage):
        paths = [storage.manifest, *storage.directory.rglob('*')]
        return {str(path.relative_to(storage.manifest.parent)): path.read_bytes()
                for path in paths if path.is_file()}

    async def test_missing_job_record_does_not_become_an_empty_queue(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        path = RAGJobs(self.rag)._path(job['id']) / 'job.json'
        path.unlink()
        with self.assertRaises((ValueError, OSError)):
            await self.rag.ajobs()
        self.assertFalse(path.exists())

    async def test_missing_live_lease_blocks_second_actual_model_effect(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        first = asyncio.create_task(self.rag.arun_job(job['id']))
        second = None
        try:
            await asyncio.wait_for(self.entered.wait(), 10)
            lease = RAGJobs(self.rag)._path(job['id']) / 'worker.lock'
            lease.unlink()
            second = asyncio.create_task(self.rag.arun_job(job['id'], retry=True))
            try:
                await asyncio.wait_for(second, 10)
            except (ValueError, OSError):
                pass
            self.assertEqual(self.effects.read_text(), 'called\n',
                             'a lost live worker lease allowed a second actual model call')
            self.assertFalse(lease.exists(), 'read/retry recreated the missing lease')
        finally:
            self.release.set()
            await asyncio.wait_for(asyncio.gather(first, *([second] if second else []),
                                                   return_exceptions=True), 15)

    async def test_empty_creation_and_batch_enqueue_declare_jobs_and_lease_identity(self):
        storage = await self.storage()
        self.assertEqual(storage.inventory()[1], {})
        self.assertTrue(storage.directory.is_dir())
        job = await self.rag.aenqueue_documents([
            {'identifier': 'one', 'title': 'One', 'content': 'backup'},
            {'identifier': 'two', 'title': 'Two', 'content': 'backup'}])
        lease = storage.path(job['id']) / storage.LEASE
        self.assertEqual(lease.read_bytes(), job['id'].encode())
        self.assertEqual((await self.rag.ajobs())[0]['document_ids'], ['one', 'two'])
        await self.rag.acancel_job(job['id'])
        self.assertEqual((await self.rag.ajob(job['id']))['status'], 'cancelled')
        self.assertEqual(lease.read_bytes(), job['id'].encode())
        self.assertEqual(self.calls, 0)

    async def test_record_catalog_root_input_and_lease_loss_fail_without_recreation(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        storage = await self.storage()
        directory = storage.path(job['id'])
        backup = self.root / 'stored-copy'
        shutil.copytree(storage.manifest.parent, backup)
        paths = (storage.manifest, storage.directory, directory, directory / 'job.json',
                 directory / storage.LEASE)
        for path in paths:
            with self.subTest(path=path.name):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                before = self.snapshot(storage)
                for operation in (lambda: self.rag.ajobs(), lambda: self.rag.ajob(job['id']),
                                  lambda: self.rag.arun_job(job['id'], retry=True),
                                  lambda: self.rag.acheckpoint_plan(job['id']),
                                  lambda: self.project.aclone(),
                                  lambda: self.project.abackup(self.root / 'backup')):
                    with self.assertRaises((ValueError, OSError)):
                        await operation()
                record = await self.project.aget_data()
                with self.assertRaises((ValueError, OSError)):
                    self.component.validate_backup(record)
                with self.assertRaises((ValueError, OSError)):
                    self.component.maintenance(record)
                with self.assertRaises((ValueError, OSError)):
                    self.component.initialize(record)
                self.assertFalse(path.exists())
                self.assertEqual(self.snapshot(storage), before)
                self.assertEqual(self.calls, 0)
                self.assertFalse((self.root / 'backup').exists())
                self.assertEqual(len(await self.app.projects.alist()), 1)
                shutil.rmtree(storage.manifest.parent)
                shutil.copytree(backup, storage.manifest.parent)

    async def test_missing_input_remains_missing_and_reports_blocked_resume(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        storage = await self.storage()
        path = storage.path(job['id']) / 'input.json'
        path.unlink()
        before = self.snapshot(storage)
        plan = await self.rag.aresume_plan(job['id'])
        self.assertEqual(plan.state, 'missing')
        self.assertFalse(plan.can_resume)
        self.assertEqual((await self.rag.ajob(job['id']))['id'], job['id'])
        with self.assertRaises((ValueError, OSError)):
            await self.rag.arun_job(job['id'])
        for operation in (lambda: self.project.aclone(),
                          lambda: self.project.abackup(self.root / 'backup')):
            with self.assertRaises((ValueError, OSError)):
                await operation()
        self.assertEqual(self.snapshot(storage), before)
        self.assertFalse(path.exists())
        self.assertEqual(self.calls, 0)

    async def test_disabled_owner_cannot_reselect_backup_or_remove_a_lost_job(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        storage = await self.storage()
        await self.project.components.aremove('rag')
        shutil.rmtree(storage.path(job['id']))
        before = self.snapshot(storage)
        for operation in (lambda: self.project.components.aselect(['rag']),
                          lambda: self.project.abackup(self.root / 'backup'),
                          lambda: self.project.components.aremove('rag', permanent=True)):
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(self.snapshot(storage), before)
        self.assertFalse((self.root / 'backup').exists())

    async def test_manifest_fields_owner_revision_and_unknown_jobs_are_required(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        storage = await self.storage()
        original = json.loads(storage.manifest.read_bytes())
        cases = [{k: v for k, v in original.items() if k != key} for key in original]
        cases += [{**original, 'schema_version': True}, {**original, 'schema_version': 2},
                  {**original, 'project_id': 'another'}, {**original, 'component': 'memory'},
                  {**original, 'jobs': []}, {**original, 'jobs': {job['id']: 'bad'}},
                  {**original, 'jobs': {}}, {**original, 'extra': None}]
        for value in cases:
            with self.subTest(value=value):
                storage.manifest.write_text(json.dumps(value))
                before = self.snapshot(storage)
                with self.assertRaises(ValueError):
                    await self.rag.ajobs()
                self.assertEqual(self.snapshot(storage), before)
        storage.manifest.write_text(json.dumps(original))
        extra = storage.directory / 'undeclared'
        extra.mkdir()
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            await self.rag.ajobs()
        self.assertTrue(extra.exists())

    async def test_current_job_fields_are_required_even_with_matching_catalog_revision(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        storage = await self.storage()
        path = storage.path(job['id']) / 'job.json'
        manifest = json.loads(storage.manifest.read_bytes())
        cases = [{k: v for k, v in job.items() if k != key} for key in job]
        cases += [{**job, 'status': []}, {**job, 'created_at': None},
                  {**job, 'created_at': '2026-10-21T00:00:00'}, {**job, 'phase': ''},
                  {**job, 'document_ids': [job['document_id']]}, {**job, 'input_digest': 'bad'}]
        for value in cases:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                manifest['jobs'][job['id']] = revision_token(value)
                storage.manifest.write_text(json.dumps(manifest))
                before = self.snapshot(storage)
                with self.assertRaises(ValueError):
                    await self.rag.ajob(job['id'])
                self.assertEqual(self.snapshot(storage), before)

    async def test_manifest_write_failure_restores_create_cancel_and_delete(self):
        from llm.components.rag import job_storage
        storage = await self.storage()
        original = job_storage.atomic_json
        def fail(path, value):
            if path == storage.manifest:
                raise OSError('manifest failed')
            return original(path, value)
        before = self.snapshot(storage)
        with patch.object(job_storage, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
            await self.rag.aenqueue_document(title='Manual', content='backup')
        self.assertEqual(self.snapshot(storage), before)
        self.assertEqual(await self.rag.ajobs(), [])
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        before = self.snapshot(storage)
        with patch.object(job_storage, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
            await self.rag.acancel_job(job['id'])
        self.assertEqual(self.snapshot(storage), before)
        await self.rag.acancel_job(job['id'])
        plan = await self.rag.acheckpoint_plan(job['id'])
        before = self.snapshot(storage)
        with patch.object(job_storage, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
            await self.rag.adelete_job(job['id'], expected_revision=plan.revision)
        self.assertEqual(self.snapshot(storage), before)
        self.assertEqual((await self.rag.ajob(job['id']))['status'], 'cancelled')

    async def test_lease_identity_links_and_fifo_cannot_authorize_execution(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        storage = await self.storage()
        path = storage.path(job['id']) / storage.LEASE
        for content in (b'', b'another-job'):
            path.write_bytes(content)
            with self.assertRaises(ValueError):
                await self.rag.arun_job(job['id'])
            self.assertEqual(path.read_bytes(), content)
        path.unlink()
        path.symlink_to('input.json')
        with self.assertRaises(ValueError):
            await self.rag.arun_job(job['id'])
        self.assertTrue(path.is_symlink())
        path.unlink()
        os.mkfifo(path)
        code = textwrap.dedent('''
            import sys
            from pathlib import Path
            from types import SimpleNamespace
            from llm.components.rag.job_storage import JobStorage
            from llm.services.infrastructure.storage import reject_links
            owner = SimpleNamespace(name='rag', root=lambda project: Path(sys.argv[1]), _checked=reject_links)
            try:
                JobStorage(SimpleNamespace(id=sys.argv[2]), owner).lease(sys.argv[3])
            except ValueError:
                raise SystemExit(0)
            raise SystemExit(1)
        ''')
        child = subprocess.run([sys.executable, '-c', code, str(storage.manifest.parent),
                                storage.project_id, job['id']], timeout=10, capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertTrue(path.exists())
        self.assertEqual(self.calls, 0)

    async def test_process_death_preserves_job_lease_and_manifest_commit_boundaries(self):
        code = textwrap.dedent('''
            import os, sys
            from pathlib import Path
            from llm.components.rag import RAGComponent, EmbeddingModel, job_storage
            from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
            from tests.llm.configuration_fixtures import rag_project
            from tests.llm.test_rag_components import Extractor, fake_embedding
            root, operation, boundary, witness = sys.argv[1:]
            component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', embedding_fn=fake_embedding), extractor=Extractor())
            projects = ProjectManager(ProjectRepository(Path(root)), components=[component])
            project = projects.create('child', components=['rag'], config=rag_project())
            data = projects.component(project, 'rag')
            if operation != 'create':
                job = data.enqueue_document(title='Manual', content='backup')
                if operation == 'delete':
                    data.cancel_job(job['id'])
                    plan = data.checkpoint_plan(job['id'])
            write, lease, remove = job_storage.atomic_json, job_storage.atomic_bytes, job_storage.remove_named_tree
            def crash(label):
                Path(witness).write_text(label)
                os._exit(77)
            def crash_write(path, value):
                write(path, value)
                if path.name == 'job.json' and boundary == 'record':
                    crash('record')
                if path.name == job_storage.JobStorage.MANIFEST and boundary == 'manifest':
                    crash('manifest')
            def crash_lease(path, value):
                lease(path, value)
                if boundary == 'lease':
                    crash('lease')
            def crash_remove(*args):
                remove(*args)
                if boundary == 'record':
                    crash('record')
            job_storage.atomic_json, job_storage.atomic_bytes, job_storage.remove_named_tree = crash_write, crash_lease, crash_remove
            if operation == 'create':
                data.enqueue_document(title='Manual', content='backup')
            elif operation == 'cancel':
                data.cancel_job(job['id'])
            else:
                data.delete_job(job['id'], expected_revision=plan.revision)
            crash('committed')
        ''')
        from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
        for operation in ('create', 'cancel', 'delete'):
            boundaries = ('lease', 'record', 'manifest', 'committed') if operation == 'create' else (
                'record', 'manifest', 'committed')
            for boundary in boundaries:
                with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as directory:
                    root, witness = Path(directory) / 'repo', Path(directory) / 'witness'
                    child = subprocess.run([sys.executable, '-c', code, str(root), operation, boundary, str(witness)],
                                           timeout=15, capture_output=True, text=True)
                    self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                    self.assertEqual(witness.read_text(), boundary)
                    projects = ProjectManager(ProjectRepository(root), components=[self.component])
                    project, = projects.list()
                    jobs = projects.component(project, 'rag').jobs()
                    expected = [] if operation == 'create' else ['queued' if operation == 'cancel' else 'cancelled']
                    if boundary == 'committed':
                        expected = [] if operation == 'delete' else ['queued' if operation == 'create' else 'cancelled']
                    self.assertEqual([job['status'] for job in jobs], expected)
                    storage = JobStorage(project, self.component)
                    for job in jobs:
                        self.assertEqual((storage.path(job['id']) / storage.LEASE).read_bytes(), job['id'].encode())


if __name__ == '__main__':
    unittest.main(verbosity=2)
