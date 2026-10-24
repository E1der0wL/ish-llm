"""RAG publication evidence protects catalog reads, source preservation and recovery."""
from llm.components.rag.corpus_storage import CorpusStorage
from llm.components.rag.publication_records import CorpusCatalog

from llm.providers.operations import LiteLLMOperations
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from tests.llm import test_rag_storage as storage
from tests.llm import test_rag_corpus_integrity as corpus_tests
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.rag import corpus_storage as owner
from llm.services.infrastructure.storage import revision_token
from llm.llm import LargeLanguageModel
from tests.llm.test_rag_components import Extractor


class CatalogLossProbe(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = storage.RAGStorageTests.asyncSetUp
    add = storage.RAGStorageTests.add
    snapshot_files = storage.RAGStorageTests.snapshot_files

    async def catalog(self):
        await self.add(identifier='original')
        root = self.project.paths.root / 'rag'
        generation = json.loads((root / 'active.json').read_bytes())['generation']
        return root / 'generations' / generation / 'catalog.json'

    async def test_missing_catalog_is_not_a_valid_backup(self):
        path = await self.catalog()
        path.unlink()
        before = self.snapshot_files()
        with self.assertRaises((ValueError, OSError)):
            await self.project.abackup(self.root / 'invalid-backup')
        self.assertEqual(self.snapshot_files(), before)
        self.assertFalse((self.root / 'invalid-backup').exists())

    async def test_empty_catalog_cannot_hide_a_published_document(self):
        path = await self.catalog()
        path.write_text('[]')
        before = self.snapshot_files()
        with self.assertRaises(ValueError):
            await self.rag.alist_catalog(limit=10)
        self.assertEqual(self.snapshot_files(), before)


class CatalogIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await corpus_tests.CorpusContractTests.asyncSetUp(self)
        self.rag_root = self.project.paths.root / 'rag'
        self.control = self.rag_root / CorpusStorage.CONTROL_FILE
        self.catalog = self.corpus.parent / CorpusStorage.CATALOG_FILE

    snapshot_files = storage.RAGStorageTests.snapshot_files

    async def reject(self, *, catalog=True):
        before = self.snapshot_files()
        operations = [self.rag.alist_documents,
                      lambda: self.rag.acompact([], expected_version=self.compaction["version"]),
                      lambda: self.rag.aget_document(self.identifier),
                      lambda: self.rag.aadd_document(title='New', content='backup'),
                      lambda: self.rag.aupdate_document(self.identifier, title='Changed'),
                      lambda: self.project.components.aselect(['rag']),
                      lambda: self.project.abackup(self.root / 'invalid-backup'), self.project.aclone]
        if catalog:
            operations.append(lambda: self.rag.alist_catalog(limit=10))
        for operation in operations:
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(self.snapshot_files(), before)
            self.assertEqual(self.effects.read_text(), 'called\n')
        self.assertFalse((self.root / 'invalid-backup').exists())
        self.assertEqual(len(await self.app.projects.alist()), 1)

    async def test_control_requires_current_version_scope_and_all_published_evidence(self):
        original = self.control.read_bytes()
        value = json.loads(original)
        cases = [None, [], {}, {**value, 'extra': True}]
        cases.extend({key: item for key, item in value.items() if key != missing} for missing in value)
        for key, replacement in (('schema_version', True), ('schema_version', 1.0), ('schema_version', 2),
                                 ('project_id', 'other'), ('component', 'other'),
                                 ('identity_revision', '0' * 64), ('active_revision', '0' * 64),
                                 ('corpus_revision', None), ('catalog_revision', None),
                                 ('corpus_revision', True), ('catalog_revision', 'invalid')):
            cases.append({**value, key: replacement})
        for index, changed in enumerate(cases):
            with self.subTest(case=index):
                if changed is None:
                    self.control.unlink()
                else:
                    self.control.write_text(json.dumps(changed))
                await self.reject()
                self.control.write_bytes(original)

    async def test_catalog_loss_and_malformed_rows_never_hide_originals(self):
        original, proof = self.catalog.read_bytes(), self.control.read_bytes()
        rows = json.loads(original)
        cases = [None, {}, [], [None], rows + rows]
        for missing in CorpusCatalog.REQUIRED:
            cases.append([{key: value for key, value in rows[0].items() if key != missing}])
        for key, value in (('title', ''), ('title', False), ('revision', True), ('revision', 1.0),
                           ('generation', 'invalid'), ('description', None), ('unknown', True)):
            cases.append([{**rows[0], key: value}])
        for index, changed in enumerate(cases):
            with self.subTest(case=index):
                if changed is None:
                    self.catalog.unlink()
                else:
                    self.catalog.write_text(json.dumps(changed))
                    # Malformed rows still fail when the hash matches. Empty rows must
                    # fail against the original publication proof, as in the baseline.
                    if changed:
                        control = json.loads(proof)
                        control['catalog_revision'] = revision_token(changed)
                        self.control.write_text(json.dumps(control))
                await self.reject()
                self.catalog.write_bytes(original)
                self.control.write_bytes(proof)

    async def test_replayed_pointer_and_valid_but_replaced_identity_cannot_delete_newest_source(self):
        active = self.rag_root / CorpusStorage.ACTIVE_FILE
        identity = self.rag_root / CorpusStorage.IDENTITY_FILE
        old_pointer = active.read_bytes()
        old_generation = self.corpus.parent
        await self.rag.aupdate_document(self.identifier, title='Newest')
        newest = active.read_bytes()
        self.assertTrue(old_generation.is_dir())
        active.write_bytes(old_pointer)
        await self.reject()
        active.write_bytes(newest)
        original_identity = identity.read_bytes()
        identity.write_text(json.dumps({'id': 'f' * 32}))
        await self.reject()
        identity.write_bytes(original_identity)
        self.assertEqual((await self.rag.aget_document(self.identifier))['title'], 'Newest')
        plan = await self.rag.acompaction_plan()
        await self.rag.acompact([old_generation.name], expected_version=plan["version"])
        self.assertFalse(old_generation.exists())

    async def test_required_evidence_refuses_links_directories_and_fifos_before_read(self):
        outside = self.root / 'outside.json'
        outside.write_text('{}')
        for path in (self.control, self.catalog, self.corpus,
                     self.rag_root / CorpusStorage.IDENTITY_FILE, self.rag_root / CorpusStorage.ACTIVE_FILE):
            original = path.read_bytes()
            for kind in ('directory', 'link', 'fifo'):
                with self.subTest(path=path.name, kind=kind):
                    path.unlink()
                    if kind == 'directory':
                        path.mkdir()
                    elif kind == 'link':
                        path.symlink_to(outside)
                    else:
                        os.mkfifo(path)
                    await self.reject()
                    if kind == 'directory':
                        path.rmdir()
                    else:
                        path.unlink()
                    path.write_bytes(original)
        self.assertEqual(outside.read_text(), '{}')

    async def test_corpus_and_catalog_semantics_must_agree_even_with_matching_revisions(self):
        rows = json.loads(self.catalog.read_bytes())
        rows[0]['title'] = 'Not the source title'
        self.catalog.write_text(json.dumps(rows))
        control = json.loads(self.control.read_bytes())
        control['catalog_revision'] = revision_token(rows)
        self.control.write_text(json.dumps(control))
        await self.reject(catalog=False)

    async def test_valid_corpus_mutation_is_not_hidden_by_cache_or_an_unchanged_catalog(self):
        from tests.llm.configuration_fixtures import rag_settings
        await self.rag.aconfigure(rag_settings({'config': {'snapshot_cache_max_bytes': 1000000}}))
        await self.rag.aget_document(self.identifier)
        value = deepcopy(self.original)
        value['documents'][self.identifier]['metadata'] = {'changed': True}
        self.corpus.write_text(json.dumps(value))
        await self.reject(catalog=False)

    async def test_catalog_reads_no_corpus_and_still_detects_changed_publication(self):
        read = owner.read_json
        def forbid_corpus(path):
            if path.name == CorpusStorage.CORPUS_FILE:
                raise AssertionError('catalog parsed corpus')
            return read(path)
        with patch.object(owner, 'read_json', side_effect=forbid_corpus):
            rows = (await self.rag.alist_catalog(limit=1))['documents']
            self.assertEqual(rows[0]['id'], self.identifier)
            self.catalog.write_text('[]')
            with self.assertRaisesRegex(ValueError, 'catalog integrity'):
                await self.rag.alist_catalog(limit=1)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_publication_failure_rolls_back_pointer_manifest_and_new_generation(self):
        write = owner.atomic_json
        before = self.snapshot_files()
        for boundary in (CorpusStorage.CATALOG_FILE, CorpusStorage.CORPUS_FILE,
                         CorpusStorage.ACTIVE_FILE, CorpusStorage.CONTROL_FILE):
            with self.subTest(boundary=boundary):
                def fail(path, value):
                    write(path, value)
                    if path.name == boundary:
                        raise OSError('injected after publication write')
                with patch.object(owner, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
                    await self.rag.aupdate_document(self.identifier, title='Uncommitted')
                self.assertEqual(self.snapshot_files(), before)
                self.assertEqual((await self.rag.aget_document(self.identifier))['title'], 'Manual')
                self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_owner_rechecks_required_catalog_before_publishing_a_prepared_snapshot(self):
        project = await self.project.aget_data()
        configured = self.component.with_config(project)
        previous = configured.snapshot(project)
        documents = deepcopy(previous['documents'])
        documents[self.identifier]['title'] = 'Replacement'
        documents[self.identifier]['revision'] += 1
        self.catalog.unlink()
        before = self.snapshot_files()
        with self.assertRaises((ValueError, OSError)):
            with self.app.project_manager.ownership.scope():
                configured.publish(project, previous, documents)
        self.assertEqual(self.snapshot_files(), before)
        self.assertEqual(self.effects.read_text(), 'called\n')


class PublicationCrashTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_death_keeps_complete_old_or_committed_new_publication(self):
        code = textwrap.dedent('''
            import asyncio, hashlib, json, os, sys
            from pathlib import Path
            from llm.providers.operations import LiteLLMOperations
            from unittest.mock import patch
            from llm.llm import LargeLanguageModel
            from llm.components.rag import RAGComponent, EmbeddingModel, corpus_storage as owner
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
                    project = await app.projects.acreate('child', components=['rag'], config=rag_project())
                    rag = project.components.rag
                    await rag.aadd_document(identifier='doc', title='Original', content='backup')
                    data = await project.aget_data()
                    configured = component.with_config(data)
                    source = component.root(data)
                    before = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in source.rglob('*') if path.is_file()}
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id, 'before': before}))
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    write = owner.atomic_json
                    def crash_write(path, value):
                        write(path, value)
                        if path.name == boundary:
                            crash(boundary)
                    with app.project_manager.ownership.scope():
                        previous = configured.snapshot(data)
                        from copy import deepcopy
                        documents = deepcopy(previous['documents'])
                        documents['doc']['title'], documents['doc']['revision'] = 'Committed', 2
                        with patch.object(owner, 'atomic_json', side_effect=crash_write):
                            configured.publish(data, previous, documents)
                    crash('committed')
            asyncio.run(main())
        ''')
        for boundary in (CorpusStorage.CATALOG_FILE, CorpusStorage.CORPUS_FILE,
                         CorpusStorage.ACTIVE_FILE, CorpusStorage.CONTROL_FILE, 'committed'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                child = subprocess.run([sys.executable, '-c', code, directory, boundary],
                                       timeout=30, capture_output=True, text=True)
                self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                self.assertEqual((root / 'reached').read_text(), boundary)
                context = json.loads((root / 'context.json').read_text())
                async def forbidden(**request):
                    with (root / 'effects').open('a') as stream:
                        stream.write('unexpected replay\n')
                    raise AssertionError('Reopening cannot invoke a model')
                component = RAGComponent(embedding=EmbeddingModel(model='test/embedding', adapter=LiteLLMOperations(call=forbidden)), extractor=Extractor())
                async with LargeLanguageModel(root / 'workspace', engines={}, components=[component]) as app:
                    project = await app.projects.aload(context['project_id'])
                    rag = project.components.rag
                    document = await rag.aget_document('doc')
                    self.assertEqual((document['title'], document['revision']),
                                     ('Committed', 2) if boundary == 'committed' else ('Original', 1))
                    self.assertEqual((await rag.alist_catalog(limit=1))['documents'][0]['title'], document['title'])
                    source = project.paths.root / 'rag'
                    after = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in source.rglob('*') if path.is_file()}
                    if boundary != 'committed':
                        self.assertEqual(after, context['before'])
                    else:
                        # Publication preserves every previous generation byte until explicit selection.
                        previous = {key: value for key, value in context["before"].items()
                                    if key.startswith("generations/")}
                        self.assertEqual({key: after[key] for key in previous}, previous)
                        plan = await rag.acompaction_plan()
                        await rag.acompact([row["generation"] for row in plan["candidates"]],
                                          expected_version=plan["version"])
                        self.assertEqual(len(list((source / CorpusStorage.GENERATIONS_DIRECTORY).iterdir())), 1)
                    await project.abackup(root / 'backup')
                    self.assertEqual((root / 'effects').read_text(), 'called\n')
