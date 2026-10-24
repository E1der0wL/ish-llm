"""Applications select retired generations; publication and inspection never delete them."""
from llm.components.rag.corpus_storage import CorpusStorage

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

from llm.components.rag import RAGComponent
from llm.services.infrastructure.storage import tree_snapshot
from tests.llm import test_rag_components as fixtures


class RAGRetentionPolicyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.RAGTests.asyncSetUp
    add = fixtures.RAGTests.add

    def active(self):
        root = self.project.paths.root / 'rag'
        return json.loads((root / CorpusStorage.ACTIVE_FILE).read_text())['generation']

    def generation(self, identifier):
        return self.project.paths.root / 'rag' / CorpusStorage.GENERATIONS_DIRECTORY / identifier

    async def publish_twice(self):
        await self.add(identifier='original')
        old = self.active()
        await self.rag.aupdate_document('original', content='New explicit source')
        return old, self.active()

    async def test_publication_never_selects_old_generations_for_deletion(self):
        await self.add(identifier='original')
        old = self.active()
        before = tree_snapshot(self.generation(old))
        await self.rag.aupdate_document('original', content='New explicit source')
        self.assertNotEqual(self.active(), old)
        self.assertTrue(self.generation(old).is_dir(), 'publication deleted an unselected generation')
        self.assertEqual(tree_snapshot(self.generation(old)), before)
        self.assertEqual((await self.rag.aget_document('original'))['content'], 'New explicit source')

    async def test_read_only_plan_and_explicit_subset_preserve_other_generations(self):
        first, second = await self.publish_twice()
        await self.rag.aupdate_document('original', title='Third publication')
        active = self.active()
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        plan = await self.rag.acompaction_plan()
        self.assertEqual(tree_snapshot(root), before)
        self.assertEqual({row['generation'] for row in plan['candidates']}, {first, second})
        self.assertIn(active, {row['generation'] for row in plan['protected']})
        preserved = {identifier: tree_snapshot(self.generation(identifier)) for identifier in (second, active)}
        await self.rag.acompact([first], expected_version=plan['version'])
        self.assertFalse(self.generation(first).exists())
        self.assertEqual({identifier: tree_snapshot(self.generation(identifier)) for identifier in preserved}, preserved)
        self.assertEqual(self.active(), active)

    async def test_stale_plan_cannot_delete_after_publication_or_source_change(self):
        old, active = await self.publish_twice()
        plan = await self.rag.acompaction_plan()
        await self.rag.aupdate_document('original', title='Changed after inspection')
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            await self.rag.acompact([old], expected_version=plan['version'])
        self.assertEqual(tree_snapshot(root), before)
        plan = await self.rag.acompaction_plan()
        artifact = self.generation(old) / CorpusStorage.CORPUS_FILE
        artifact.write_bytes(artifact.read_bytes() + b'\n')
        before = tree_snapshot(root)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            await self.rag.acompact([old], expected_version=plan['version'])
        self.assertEqual(tree_snapshot(root), before)
        self.assertTrue(self.generation(active).exists())

    async def test_targets_and_revision_are_explicit_and_active_generation_is_protected(self):
        old, active = await self.publish_twice()
        plan = await self.rag.acompaction_plan()
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        for targets, version in (([active], plan['version']), ([old, old], plan['version']),
                                 (['../outside'], plan['version']), (['0' * 32], plan['version']),
                                 ([old], None), (old, plan['version'])):
            with self.subTest(targets=targets, version=version), self.assertRaises(ValueError):
                await self.rag.acompact(targets, expected_version=version)
            self.assertEqual(tree_snapshot(root), before)
        await self.rag.acompact([], expected_version=plan['version'])
        self.assertEqual(tree_snapshot(root), before)

    async def test_corrupt_retired_source_refuses_inspection_and_deletion_in_place(self):
        old, active = await self.publish_twice()
        plan = await self.rag.acompaction_plan()
        path = self.generation(old) / CorpusStorage.CORPUS_FILE
        path.write_bytes(b'{corrupt retired source')
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        for operation in (self.rag.acompaction_plan,
                          lambda: self.rag.acompact([old], expected_version=plan['version'])):
            with self.assertRaises((ValueError, OSError)):
                await operation()
            self.assertEqual(tree_snapshot(root), before)
        self.assertEqual(self.active(), active)

    async def test_failure_after_one_selected_removal_rolls_back_all_sources(self):
        first, second = await self.publish_twice()
        await self.rag.aupdate_document('original', title='Active')
        plan = await self.rag.acompaction_plan()
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        from llm.components.rag import corpus_storage as owner
        remove = owner.remove_named_tree
        calls = []
        def fail_after_remove(*args):
            remove(*args)
            calls.append(args[-1])
            raise OSError('after selected removal')
        with patch.object(owner, 'remove_named_tree', side_effect=fail_after_remove):
            with self.assertRaisesRegex(OSError, 'after selected removal'):
                await self.rag.acompact([first, second], expected_version=plan['version'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(tree_snapshot(root), before)

    async def test_unknown_entries_links_and_nonregular_sources_are_never_deleted(self):
        old, active = await self.publish_twice()
        plan = await self.rag.acompaction_plan()
        root = self.generation(old)
        before = tree_snapshot(root)
        outside = self.root / 'outside'
        outside.write_bytes(b'not owned by RAG')
        for kind in ('unknown', 'symlink', 'fifo'):
            with self.subTest(kind=kind):
                path = root / ('unknown' if kind == 'unknown' else 'chroma/unsafe')
                if kind == 'unknown':
                    path.write_bytes(b'unknown origin')
                elif kind == 'symlink':
                    path.symlink_to(outside)
                else:
                    os.mkfifo(path)
                for operation in (self.rag.acompaction_plan,
                                  lambda: self.rag.acompact([old], expected_version=plan['version'])):
                    with self.assertRaises((ValueError, OSError)):
                        await operation()
                    self.assertTrue(path.exists())
                path.unlink()
                self.assertEqual(tree_snapshot(root), before)
                self.assertEqual(outside.read_bytes(), b'not owned by RAG')
        self.assertEqual(self.active(), active)

    async def test_concurrent_requests_recheck_the_same_plan(self):
        old, active = await self.publish_twice()
        plan = await self.rag.acompaction_plan()
        preserved = tree_snapshot(self.generation(active))
        results = await asyncio.gather(*(self.rag.acompact([old], expected_version=plan['version'])
                                        for _ in range(2)), return_exceptions=True)
        self.assertEqual(sum(value is None for value in results), 1)
        self.assertEqual(sum(isinstance(value, ValueError) for value in results), 1)
        self.assertFalse(self.generation(old).exists())
        self.assertEqual(tree_snapshot(self.generation(active)), preserved)

    async def test_cancellation_drains_the_complete_selected_transaction(self):
        first, second = await self.publish_twice()
        await self.rag.aupdate_document('original', title='Active')
        active = self.active()
        preserved = tree_snapshot(self.generation(active))
        plan = await self.rag.acompaction_plan()
        from llm.components.rag import corpus_storage as owner
        remove = owner.remove_named_tree
        entered, finish = threading.Event(), threading.Event()
        def paused(*args):
            remove(*args)
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('removal was not released')
        with patch.object(owner, 'remove_named_tree', side_effect=paused):
            task = asyncio.create_task(self.rag.acompact([first, second], expected_version=plan['version']))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                task.cancel()
            finally:
                finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.generation(first).exists())
        self.assertFalse(self.generation(second).exists())
        self.assertEqual(tree_snapshot(self.generation(active)), preserved)
        self.assertEqual((await self.rag.acompaction_plan())['candidates'], [])

    async def test_technical_recovery_finishes_only_existing_intents_and_protects_active(self):
        from llm.services.infrastructure import storage
        first, second = await self.publish_twice()
        await self.rag.aupdate_document('original', title='Active')
        active = self.active()
        root = self.generation(first).parent
        preserved = {name: tree_snapshot(self.generation(name)) for name in (second, active)}
        # Produce a genuine interrupted standalone deletion intent, before recovery.
        with patch.object(storage.shutil, 'rmtree', side_effect=OSError('interrupted deletion')):
            with self.assertRaisesRegex(OSError, 'interrupted deletion'):
                storage.remove_named_tree(root, self.generation(first), first)
        before = tree_snapshot(root)
        with self.assertRaisesRegex(ValueError, 'must be recovered'):
            await self.rag.acompaction_plan()
        self.assertEqual(tree_snapshot(root), before)
        self.assertEqual(await self.rag.arecover_compaction(), [first])
        self.assertFalse(self.generation(first).exists())
        self.assertEqual({name: tree_snapshot(self.generation(name)) for name in preserved}, preserved)
        self.assertEqual(await self.rag.arecover_compaction(), [])
        with patch.object(storage.shutil, 'rmtree', side_effect=OSError('interrupted deletion')):
            with self.assertRaises(OSError):
                storage.remove_named_tree(root, self.generation(active), active)
        before = tree_snapshot(root)
        with self.assertRaisesRegex(ValueError, 'protected'):
            await self.rag.arecover_compaction()
        self.assertEqual(tree_snapshot(root), before)

    async def test_process_death_preserves_all_sources_or_complete_selection(self):
        code = textwrap.dedent('''
            import asyncio, json, os, sys
            from pathlib import Path
            from llm.providers.operations import LiteLLMOperations
            from llm.llm import LargeLanguageModel
            from llm.components.rag import RAGComponent, EmbeddingModel
            from llm.components.rag import corpus_storage as owner
            from llm.services.infrastructure import transactions
            from llm.services.infrastructure.storage import tree_snapshot
            from tests.llm.configuration_fixtures import rag_project
            from tests.llm.test_rag_components import fake_embedding, Extractor
            root, boundary = Path(sys.argv[1]), sys.argv[2]
            async def main():
                component = RAGComponent(embedding=EmbeddingModel(model='test/embedding',
                                         adapter=LiteLLMOperations(call=fake_embedding)), extractor=Extractor())
                async with LargeLanguageModel(root / 'workspace', components=[component]) as app:
                    project = await app.projects.acreate(components=['rag'], config=rag_project())
                    data = project.components['rag']
                    await data.aadd_document(identifier='original', title='Original', content='backup')
                    await data.aupdate_document('original', content='Alice')
                    await data.aupdate_document('original', title='Active')
                    plan = await data.acompaction_plan()
                    selected = [row['generation'] for row in plan['candidates']]
                    rag = project.paths.root / 'rag'
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id,
                        'plan': plan, 'before': tree_snapshot(rag), 'selected': selected}))
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    remove, write = owner.remove_named_tree, transactions._write
                    count = 0
                    def cut_remove(*args):
                        nonlocal count
                        remove(*args)
                        count += 1
                        if boundary == str(count):
                            crash(boundary)
                    def cut_write(path, value):
                        write(path, value)
                        if boundary == 'marker' and path.name == 'COMMITTED':
                            crash('marker')
                    owner.remove_named_tree, transactions._write = cut_remove, cut_write
                    await data.acompact(selected, expected_version=plan['version'])
                    crash('committed')
            asyncio.run(main())
        ''')
        from llm.llm import LargeLanguageModel
        for boundary in ('1', '2', 'marker', 'committed'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                child = await asyncio.to_thread(subprocess.run,
                    [sys.executable, '-c', code, directory, boundary],
                    timeout=30, capture_output=True, text=True)
                self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                self.assertEqual((root / 'reached').read_text(), boundary)
                context = json.loads((root / 'context.json').read_text())
                async with LargeLanguageModel(root / 'workspace', components=[RAGComponent()]) as app:
                    project = await app.projects.aload(context['project_id'])
                    data = project.components['rag']
                    after = tree_snapshot(project.paths.root / 'rag')
                    expected = context['before']
                    if boundary in ('marker', 'committed'):
                        prefixes = tuple('generations/' + name + '/' for name in context['selected'])
                        expected = {key: value for key, value in expected.items() if not key.startswith(prefixes)}
                    self.assertEqual(after, expected)
                    self.assertEqual((await data.acompaction_plan())['active_generation'],
                                     context['plan']['active_generation'])
                    self.assertEqual((await data.aget_document('original'))['title'], 'Active')
