"""중복 I/O·전체 결과 변환을 줄여도 수명/커서/세대 검사를 유지하는지 검증한다."""
from tests.llm.configuration_fixtures import rag_settings, rag_project

import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from llm.core.results import ExecutionResult
from llm.llm import LargeLanguageModel
from llm.services.query import Query
from tests.llm.support.fake_engine import FakeStreamingEngine


class QueryIterationTests(unittest.TestCase):
    def test_ascending_page_stops_consuming_at_its_limit(self):
        consumed = []
        def records():
            for i in range(1000):
                consumed.append(i)
                yield SimpleNamespace(id=str(i), status="completed" if i % 2 else "failed")
        page = Query(after="2", status="completed", offset=1, limit=2).apply(records())
        self.assertEqual([row.id for row in page], ["5", "7"])
        self.assertEqual(consumed, list(range(8)))

    def test_streaming_selection_matches_materialized_contract(self):
        rows = [SimpleNamespace(run_id=str(i), status="completed" if i % 2 else "failed") for i in range(7)]
        for descending in (False, True):
            for after in (None, "0", "3", "6"):
                for status in (None, "completed", "failed", "missing"):
                    for offset in (0, 2, 8):
                        for limit in (None, 0, 2):
                            expected = list(reversed(rows)) if descending else list(rows)
                            if after is not None:
                                index = next(i for i, row in enumerate(expected) if row.run_id == after)
                                expected = expected[index + 1:]
                            if status is not None:
                                expected = [r for r in expected if r.status == status]
                            expected = expected[offset:None if limit is None else offset + limit]
                            query = Query(after=after, status=status, offset=offset, limit=limit, descending=descending)
                            self.assertEqual(query.apply(iter(rows)), expected, query)
        with self.assertRaisesRegex(ValueError, "cursor"):
            Query(after="missing", limit=0).apply(iter(rows))
        self.assertEqual(Query(limit=10 ** 30).apply(iter(rows)), rows)
        self.assertEqual(Query(offset=10 ** 30).apply(iter(rows)), [])


class FacadeReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = LargeLanguageModel(Path(temp.name), engines={"test": FakeStreamingEngine(chunks=("answer",))}, components=[])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("Reads")
        self.session = await self.project.sessions.acreate("Requests")

    async def test_request_result_loads_run_once_and_never_caches_state(self):
        request = await self.session.run.submit("hello", engine="test")
        run = await request.wait()
        await self.session.run.shutdown()
        repository = self.app.run_repository
        with patch.object(repository, "load", wraps=repository.load) as load:
            result = await request.aresult()
            self.assertEqual(result.run_id, run.id)
            self.assertEqual(load.call_count, 1)
        changed = await run.aget_data()
        changed.error = "updated observation"
        with self.app.project_manager.ownership.scope():
            repository.save(changed)
        self.assertEqual((await request.aresult()).error, "updated observation")

    async def test_response_loads_parent_session_once(self):
        run = await (await self.session.run.submit("hello", engine="test")).wait()
        await self.session.run.shutdown()
        repository = self.app.project_manager.sessions.repository
        with patch.object(repository, "load", wraps=repository.load) as load:
            self.assertEqual((await run.aresponse()).content, "answer")
            self.assertEqual(load.call_count, 1)

    async def test_result_page_converts_only_selected_runs(self):
        runs = []
        for text in ("one", "two", "three"):
            runs.append(await (await self.session.run.submit(text, engine="test")).wait())
        await self.session.run.shutdown()
        with patch.object(ExecutionResult, "from_run", wraps=ExecutionResult.from_run) as convert:
            page = await self.project.results.alist(query=Query(after=runs[0].id, limit=1))
            self.assertEqual([r.run_id for r in page], [runs[1].id])
            self.assertEqual(convert.call_count, 1)
            convert.reset_mock()
            self.assertEqual(await self.session.results.alist(query=Query(limit=0)), [])
            convert.assert_not_called()
        class ResultQuery(Query):
            def apply(self, items):
                # Query 확장에서 완료 결과의 고유 필드를 계속 사용할 수 있다.
                return [r for r in items if r.run_id == runs[1].id and r.total_tokens is None]
        custom = await self.session.results.alist(query=ResultQuery())
        self.assertEqual([r.run_id for r in custom], [runs[1].id])


@unittest.skipUnless(all(importlib.util.find_spec(n) for n in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class RAGReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from llm.components.rag import RAGComponent, EmbeddingModel
        from tests.llm.test_rag_components import Extractor, fake_embedding
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.component = RAGComponent(embedding=EmbeddingModel(model="test/embedding", embedding_fn=fake_embedding),
                                      extractor=Extractor())
        self.app = LargeLanguageModel(Path(temp.name), engines={}, components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("RAG", components=["rag"], config=rag_project())
        self.data = await self.project.components.aget("rag")
        await self.data.aadd_document(identifier="manual", title="Manual", content="Alice owns Atlas")

    async def test_reranked_query_reads_corpus_and_searches_once(self):
        from llm.components.rag import component as implementation
        self.component.reranker = SimpleNamespace(rerank=AsyncMock(return_value={
            "results": [{"index": 0, "relevance_score": 0.9}]}))
        for method in (self.data.asearch, self.data.asearch_documents):
            with patch.object(implementation, "read_json", wraps=implementation.read_json) as read, \
                 patch.object(self.component, "search", wraps=self.component.with_config(self.project.data).search) as search:
                result = await method("Alice", method="bm25", rerank=True)
                self.assertTrue(result)
                self.assertEqual(search.call_count, 1)
                self.assertEqual(sum(call.args[0].name == "corpus.json" for call in read.call_args_list), 1)

    async def test_reranking_rejects_a_changed_corpus_without_repeating_search(self):
        from llm.components.rag import RAGConflictError
        async def change(*args):
            await self.data.aupdate_document("manual", content="Alice owns Atlas. Updated.")
            return {"results": [{"index": 0, "relevance_score": 1.0}]}
        self.component.reranker = SimpleNamespace(rerank=change)
        for method in (self.data.asearch, self.data.asearch_documents):
            with patch.object(self.component, "search", wraps=self.component.with_config(self.project.data).search) as search:
                with self.assertRaises(RAGConflictError):
                    await method("Alice", method="bm25", rerank=True)
                self.assertEqual(search.call_count, 1)

    async def test_reranking_rechecks_component_selection(self):
        async def remove(*args):
            await self.project.components.aremove("rag")
            return {"results": [{"index": 0, "relevance_score": 1.0}]}
        self.component.reranker = SimpleNamespace(rerank=remove)
        with self.assertRaisesRegex(ValueError, "not enabled"):
            await self.data.asearch("Alice", method="bm25", rerank=True)

    async def test_relation_sources_index_each_document_once(self):
        class CountedChunks(list):
            iterations = 0
            def __iter__(self):
                self.iterations += 1
                return super().__iter__()
        project = self.project.data
        with self.app.project_manager.ownership.scope():
            snapshot = self.component.snapshot(project)
            doc = snapshot["documents"]["manual"]
            template = doc["chunks"][0]
            chunks = CountedChunks([{**template, "id": f"manual:c{i}"} for i in range(100)])
            doc["chunks"] = chunks
            hit = {**chunks[0], "title": doc["title"], "revision": 1, "metadata": {}}
            relations = [{"document_id": "manual", "source_id": f"manual:c{i}"} for i in range(1, 100)]
            with patch("llm.components.rag.component.related_graph", return_value={"entities": [], "relations": relations}):
                result = self.component.combined_result(project, snapshot, "Alice", [hit], max_hops=2, relation_limit=100)
        self.assertEqual(len(result["sources"]), 100)
        self.assertEqual(chunks.iterations, 1)

    async def test_compaction_does_not_delete_generations_when_active_corpus_is_unreadable(self):
        project = self.project.data
        with self.app.project_manager.ownership.scope():
            snapshot = self.component.snapshot(project)
            root = self.component.root(project) / "generations"
            inactive = root / "retained"
            inactive.mkdir()
            corpus = root / snapshot["generation"] / "corpus.json"
            original = corpus.read_bytes()
            corpus.write_text("broken JSON", encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                await self.data.acompact()
            self.assertTrue(inactive.is_dir())
        finally:
            corpus.write_bytes(original)
