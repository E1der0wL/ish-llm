"""Model-call gaps cannot hide lost or changed RAG source evidence."""
from llm.components.rag.corpus_storage import CorpusStorage

from llm.core.model_protocol import EmbeddingResult, RankedDocument, RerankResult
from types import SimpleNamespace
import json
import os
import unittest
from unittest.mock import patch
from tests.llm import test_rag_corpus_integrity as corpus
from tests.llm import test_rag_storage as storage
from tests.llm.test_rag_components import fake_embedding

class QueryEvidenceProbe(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=corpus.CorpusContractTests.asyncSetUp
    snapshot_files=storage.RAGStorageTests.snapshot_files

    async def test_catalog_lost_during_rerank_cannot_return_a_trusted_search_result(self):
        catalog=self.corpus.parent/'catalog.json'
        effects=self.root/'rerank-effects'
        after_damage={}
        async def rerank(*args):
            with effects.open('a') as stream:stream.write('ranked\n')
            catalog.unlink()
            after_damage.update(self.snapshot_files())
            return RerankResult((RankedDocument(0, 1.0),))
        self.component.reranker=SimpleNamespace(rerank=rerank)
        with self.assertRaises((ValueError,OSError,RuntimeError)):
            await self.rag.asearch('backup',method='bm25',rerank=True)
        self.assertEqual(effects.read_text(),'ranked\n')
        self.assertEqual(self.effects.read_text(),'called\n')
        self.assertEqual(self.snapshot_files(),after_damage)
        self.assertFalse(catalog.exists())

    async def test_rerank_cannot_hide_same_size_corpus_changes_even_with_restored_mtime(self):
        info = self.corpus.stat()
        original = self.corpus.read_bytes()
        self.assertIn(b'Manual', original)
        damaged = original.replace(b'Manual', b'MAnual')
        self.assertEqual(len(damaged), len(original))
        after_damage = {}
        effects = self.root / 'rerank-effects'
        async def rerank(*args):
            effects.write_text('ranked\n')
            self.corpus.write_bytes(damaged)
            os.utime(self.corpus, ns=(info.st_atime_ns, info.st_mtime_ns))
            after_damage.update(self.snapshot_files())
            return RerankResult((RankedDocument(0, 1.0),))
        self.component.reranker = SimpleNamespace(rerank=rerank)
        with self.assertRaisesRegex(RuntimeError, 'source evidence changed'):
            await self.rag.asearch_documents('backup', method='bm25', rerank=True)
        self.assertEqual(self.snapshot_files(), after_damage)
        self.assertEqual(self.corpus.read_bytes(), damaged)
        self.assertEqual(effects.read_text(), 'ranked\n')
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_query_model_gap_rejects_lost_catalog_before_index_search(self):
        catalog = self.corpus.parent / 'catalog.json'
        after_damage = {}
        effects = self.root / 'query-effects'
        async def embedding(texts, **request):
            effects.write_text('queried\n')
            catalog.unlink()
            after_damage.update(self.snapshot_files())
            return EmbeddingResult(tuple((await fake_embedding(input=texts)).data[0]["embedding"]))
        with patch.object(self.component.embedding, 'embed', side_effect=embedding):
            with self.assertRaises((ValueError, OSError, RuntimeError)):
                await self.rag.asearch_documents('backup', method='vector')
        self.assertEqual(self.snapshot_files(), after_damage)
        self.assertEqual(effects.read_text(), 'queried\n')
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_rerank_gap_cannot_hide_missing_graph_or_vector_index(self):
        effects = self.root / 'rerank-effects'
        for name in ('graph.kuzu', 'chroma'):
            with self.subTest(name=name):
                path = self.corpus.parent / name
                moved = self.root / ('held-' + name)
                after_damage = {}
                async def rerank(*args):
                    effects.write_text('ranked\n')
                    path.rename(moved)
                    after_damage.update(self.snapshot_files())
                    return RerankResult((RankedDocument(0, 1.0),))
                self.component.reranker = SimpleNamespace(rerank=rerank)
                try:
                    with self.assertRaises((ValueError, OSError, RuntimeError)):
                        await self.rag.asearch('backup', method='bm25', rerank=True)
                    self.assertEqual(self.snapshot_files(), after_damage)
                    self.assertFalse(path.exists())
                    self.assertEqual(effects.read_text(), 'ranked\n')
                    self.assertEqual(self.effects.read_text(), 'called\n')
                finally:
                    if moved.exists():
                        moved.rename(path)

    async def test_changed_publication_proof_is_checked_without_reparsing_the_corpus(self):
        control = self.project.paths.root / 'rag' / CorpusStorage.CONTROL_FILE
        after_damage = {}
        async def rerank(*args):
            value = json.loads(control.read_bytes())
            value['corpus_revision'] = 'f' * 64
            control.write_text(json.dumps(value))
            after_damage.update(self.snapshot_files())
            return RerankResult((RankedDocument(0, 1.0),))
        self.component.reranker = SimpleNamespace(rerank=rerank)
        from llm.components.rag import corpus_storage as owner
        with patch.object(owner, 'read_json', wraps=owner.read_json) as read:
            with self.assertRaisesRegex(RuntimeError, 'source evidence changed'):
                await self.rag.asearch_documents('backup', method='bm25', rerank=True)
            self.assertEqual(sum(call.args[0].name == 'corpus.json' for call in read.call_args_list), 1)
        self.assertEqual(self.snapshot_files(), after_damage)
        self.assertEqual(self.effects.read_text(), 'called\n')
