"""Lost RAG preparation receipts must not become permission to repeat completed work."""
import asyncio
import json
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


class PreparedLossTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_prepared_and_batch_receipts_do_not_repeat_a_finished_model_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effects = root / 'effects.txt'
            async def embedding(**request):
                with effects.open('a') as stream:
                    stream.write('called\n')
                return await fake_embedding(**request)
            component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', embedding_fn=embedding),
                                     extractor=Extractor())
            async with LargeLanguageModel(root / 'workspace', engines={}, components=[component]) as app:
                project = await app.projects.acreate('probe', components=['rag'], config=rag_project({
                    'parameters': {'components': {'rag': {'config': {'embedding_cache_max_bytes': 0}}}}}))
                rag = await project.components.aget('rag')
                job = await rag.aenqueue_document(title='Manual', content='backup')
                with patch.object(RAGComponent, 'publish', side_effect=OSError('publication failed')):
                    with self.assertRaises(OSError):
                        await rag.arun_job(job['id'])
                path = RAGJobs(rag)._path(job['id'])
                self.assertEqual(effects.read_text(), 'called\n')
                self.assertTrue((path / 'prepared.json').exists())
                self.assertTrue((path / 'batches').is_dir())
                (path / 'prepared.json').unlink()
                shutil.rmtree(path / 'batches')
                rejected = False
                try:
                    await rag.arun_job(job['id'], retry=True)
                except (ValueError, OSError):
                    rejected = True
                self.assertEqual(effects.read_text(), 'called\n',
                    'missing preparation receipts repeated an already completed model effect')
                self.assertTrue(rejected)
                self.assertFalse((path / 'prepared.json').exists())


class CheckpointStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.effects = self.root / 'effects.txt'
        async def embedding(**request):
            with self.effects.open('a') as stream:
                stream.write('called\n')
            return await fake_embedding(**request)
        self.component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', embedding_fn=embedding),
                                      extractor=Extractor())
        self.app = LargeLanguageModel(self.root / 'workspace', engines={}, components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate('probe', components=['rag'], config=rag_project({
            'parameters': {'components': {'rag': {'config': {'embedding_cache_max_bytes': 0}}}}}))
        self.rag = await self.project.components.aget('rag')
        self.jobs = RAGJobs(self.rag)
        self.storage = JobStorage(await self.project.aget_data(), self.component)

    async def failed_job(self):
        job = await self.rag.aenqueue_document(identifier='manual', title='Manual', content='backup')
        with patch.object(RAGComponent, 'publish', side_effect=OSError('publication failed')):
            with self.assertRaises(OSError):
                await self.rag.arun_job(job['id'])
        self.assertEqual(self.effects.read_text(), 'called\n')
        return job['id']

    def snapshot(self, identifier):
        paths = [self.storage.manifest, *self.storage.path(identifier).rglob('*')]
        return {str(path.relative_to(self.storage.manifest.parent)): path.read_bytes()
                for path in paths if path.is_file()}

    async def test_each_declared_receipt_loss_blocks_read_write_resume_and_backup(self):
        identifier = await self.failed_job()
        root = self.storage.path(identifier)
        saved = self.root / 'saved-job'
        shutil.copytree(root, saved)
        paths = [root / JobStorage.PREPARED, root / JobStorage.BATCHES,
                 *sorted((root / JobStorage.BATCHES).glob('*.json'))]
        for path in paths:
            with self.subTest(path=path.name):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
                before = self.snapshot(identifier)
                plan = await self.rag.aresume_plan(identifier)
                self.assertEqual(plan.state, 'missing')
                self.assertFalse(plan.can_resume)
                for operation in (lambda: self.rag.arun_job(identifier, retry=True),
                                  lambda: self.project.abackup(self.root / 'backup'),
                                  lambda: self.project.aclone()):
                    with self.assertRaises((ValueError, OSError)):
                        await operation()
                for operation in (lambda: self.jobs._prepared(identifier),
                                  lambda: self.jobs._batch(identifier, 'new', 'signature', {'vector': [1.0]})):
                    with self.assertRaises((ValueError, OSError)):
                        operation()
                self.assertEqual(self.effects.read_text(), 'called\n')
                self.assertEqual(self.snapshot(identifier), before)
                self.assertFalse(path.exists())
                self.assertFalse((self.root / 'backup').exists())
                self.assertEqual(len(await self.app.projects.alist()), 1)
                shutil.rmtree(root)
                shutil.copytree(saved, root)

    async def test_never_created_is_empty_but_undeclared_checkpoint_is_not_adopted(self):
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        identifier = job['id']
        before = self.snapshot(identifier)
        self.assertIsNone(self.jobs._prepared(identifier))
        self.assertIsNone(self.jobs._batch(identifier, 'new', 'signature'))
        self.assertEqual(self.snapshot(identifier), before)
        path = self.storage.path(identifier) / JobStorage.PREPARED
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Undeclared'):
            self.jobs._prepared(identifier)
        self.assertEqual(path.read_text(), '{}')
        self.assertFalse(self.effects.exists())

    async def test_matching_hash_cannot_turn_null_or_malformed_receipt_into_cache_miss(self):
        identifier = await self.failed_job()
        manifest = json.loads(self.storage.manifest.read_bytes())
        keys = manifest['jobs'][identifier]['checkpoints']
        batch = next(key for key in keys if key.startswith(JobStorage.BATCHES + '/'))
        original = self.snapshot(identifier)
        cases = [(JobStorage.PREPARED, None), (batch, None), (batch, {}),
                 (batch, {'signature': 'known', 'value': None}),
                 (batch, {'signature': False, 'value': []})]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                path = self.storage.checkpoint_path(identifier, key)
                path.write_text(json.dumps(value))
                keys[key] = revision_token(value)
                self.storage.manifest.write_text(json.dumps(manifest))
                before = self.snapshot(identifier)
                plan = await self.rag.aresume_plan(identifier)
                self.assertEqual(plan.state, 'corrupt')
                self.assertFalse(plan.can_resume)
                with self.assertRaises(ValueError):
                    await self.rag.arun_job(identifier, retry=True)
                self.assertEqual(self.effects.read_text(), 'called\n')
                self.assertEqual(self.snapshot(identifier), before)
                for name, content in original.items():
                    (self.storage.manifest.parent / name).write_bytes(content)
                manifest = json.loads(self.storage.manifest.read_bytes())
                keys = manifest['jobs'][identifier]['checkpoints']

    async def test_checkpoint_manifest_failure_restores_source_and_declaration(self):
        from llm.components.rag import job_storage
        identifier = await self.failed_job()
        value = self.storage.read_checkpoint(identifier, JobStorage.PREPARED)
        original = job_storage.atomic_json
        def fail(path, data):
            original(path, data)
            if path == self.storage.manifest:
                raise OSError('manifest flush failed')
        for operation in (lambda: self.jobs._batch(identifier, 'new', 'signature', {'vector': [1.0]}),
                          lambda: self.jobs._prepared(identifier, value)):
            before = self.snapshot(identifier)
            with patch.object(job_storage, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
                operation()
            self.assertEqual(self.snapshot(identifier), before)
            self.assertEqual(self.effects.read_text(), 'called\n')
            self.storage.checkpoints(identifier)

    async def test_failed_cleanup_preserves_committed_publication_and_complete_preparation(self):
        from llm.components.rag import job_storage
        job = await self.rag.aenqueue_document(title='Manual', content='backup')
        identifier = job['id']
        remove = job_storage.remove_named_tree
        def fail_remove(*args):
            remove(*args)
            raise OSError('cleanup failed after rename')
        with patch.object(job_storage, 'remove_named_tree', side_effect=fail_remove):
            result = await self.rag.arun_job(identifier)
        self.assertEqual(result['status'], JobStorage.COMPLETED)
        self.assertTrue(self.storage.checkpoints(identifier)[1])
        generation = self.component.corpus_version(await self.project.aget_data())
        before = self.snapshot(identifier)
        write = job_storage.atomic_json
        def fail_write(path, value):
            write(path, value)
            if path == self.storage.manifest:
                raise OSError('cleanup manifest failed')
        with patch.object(job_storage, 'atomic_json', side_effect=fail_write), self.assertRaises(OSError):
            self.jobs._cleanup(identifier)
        self.assertEqual(self.snapshot(identifier), before)
        self.assertEqual(self.component.corpus_version(await self.project.aget_data()), generation)
        self.assertFalse((await self.rag.aresume_plan(identifier)).can_resume)
        with self.assertRaises(ValueError):
            await self.rag.arun_job(identifier, retry=True)
        self.jobs._cleanup(identifier)
        self.assertEqual(self.storage.checkpoints(identifier)[1], {})
        self.assertEqual((await self.rag.ajob(identifier))['status'], JobStorage.COMPLETED)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_process_death_preserves_checkpoint_and_cleanup_commit_boundaries(self):
        code = textwrap.dedent('''
            import asyncio, json, os, sys
            from pathlib import Path
            from unittest.mock import patch
            from llm.llm import LargeLanguageModel
            from llm.components.rag import RAGComponent, EmbeddingModel, job_storage
            from llm.components.rag.jobs import RAGJobs
            from llm.services.infrastructure import storage as files
            from tests.llm.configuration_fixtures import rag_project
            from tests.llm.test_rag_components import Extractor, fake_embedding
            directory, operation, boundary = sys.argv[1:]
            root = Path(directory)
            async def embedding(**request):
                with (root / 'effects').open('a') as stream:
                    stream.write('called\\n')
                return await fake_embedding(**request)
            async def main():
                component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', embedding_fn=embedding), extractor=Extractor())
                async with LargeLanguageModel(root / 'workspace', engines={}, components=[component]) as app:
                    project = await app.projects.acreate('child', components=['rag'], config=rag_project())
                    rag = await project.components.aget('rag')
                    job = await rag.aenqueue_document(title='Manual', content='backup')
                    if operation == 'cleanup':
                        with patch.object(RAGJobs, '_cleanup', return_value=None):
                            await rag.arun_job(job['id'])
                    else:
                        with patch.object(RAGComponent, 'publish', side_effect=OSError('publication failed')):
                            try:
                                await rag.arun_job(job['id'])
                            except OSError:
                                pass
                    jobs = RAGJobs(rag)
                    owner = jobs._storage()
                    checkpoints = owner.checkpoints(job['id'])[1]
                    batch = next(value for key, value in checkpoints.items() if key.startswith(owner.BATCHES + '/'))
                    paths = [owner.manifest, *owner.path(job['id']).rglob('*')]
                    before = {str(path.relative_to(owner.manifest.parent)): path.read_bytes().hex()
                              for path in paths if path.is_file()}
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id, 'job_id': job['id'],
                        'before': before, 'generation': component.corpus_version(project.data)}))
                    write, remove, unlink = job_storage.atomic_json, job_storage.remove_named_tree, files.unlink_file
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    def crash_write(path, value):
                        write(path, value)
                        if boundary == 'record' and path.name == 'copied.json':
                            crash('record')
                        if boundary == 'manifest' and path.name == owner.MANIFEST:
                            crash('manifest')
                    def crash_remove(*args):
                        remove(*args)
                        if boundary == 'batches':
                            crash('batches')
                    def crash_unlink(path):
                        unlink(path)
                        if boundary == 'prepared' and path.name == owner.PREPARED:
                            crash('prepared')
                    job_storage.atomic_json, job_storage.remove_named_tree, files.unlink_file = crash_write, crash_remove, crash_unlink
                    if operation == 'checkpoint':
                        jobs._batch(job['id'], 'copied', batch['signature'], batch['value'])
                    else:
                        jobs._cleanup(job['id'])
                    crash('committed')
            asyncio.run(main())
        ''')
        for operation in ('checkpoint', 'cleanup'):
            boundaries = ('record', 'manifest', 'committed') if operation == 'checkpoint' else (
                'prepared', 'batches', 'manifest', 'committed')
            for boundary in boundaries:
                with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    child = subprocess.run([sys.executable, '-c', code, directory, operation, boundary],
                                           timeout=20, capture_output=True, text=True)
                    self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                    self.assertEqual((root / 'reached').read_text(), boundary)
                    context = json.loads((root / 'context.json').read_text())
                    async def forbidden(**request):
                        with (root / 'effects').open('a') as stream:
                            stream.write('unexpected replay\n')
                        raise AssertionError('Reopening cannot invoke a model')
                    component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', embedding_fn=forbidden),
                                             extractor=Extractor())
                    async with LargeLanguageModel(root / 'workspace', engines={}, components=[component]) as app:
                        project = await app.projects.aload(context['project_id'])
                        rag = await project.components.aget('rag')
                        owner = RAGJobs(rag)._storage()
                        job = await rag.ajob(context['job_id'])
                        values = owner.checkpoints(job['id'])[1]
                        self.assertEqual(job['status'], 'completed' if operation == 'cleanup' else 'failed')
                        paths = [owner.manifest, *owner.path(job['id']).rglob('*')]
                        after = {str(path.relative_to(owner.manifest.parent)): path.read_bytes().hex()
                                 for path in paths if path.is_file()}
                        if boundary != 'committed':
                            self.assertEqual(after, context['before'])
                        elif operation == 'cleanup':
                            self.assertEqual(values, {})
                        else:
                            self.assertIn(owner.BATCHES + '/copied.json', values)
                        self.assertEqual(list(component.corpus_version(project.data)), context['generation'])
                        self.assertEqual((root / 'effects').read_text(), 'called\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
