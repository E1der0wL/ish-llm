"""중복 I/O·전체 결과 변환을 줄여도 수명/커서/세대 검사를 유지하는지 검증한다."""

from llm.core.agents import AgentBinding

from llm.core.model_protocol import RankedDocument, RerankResult

from llm.providers.operations import LiteLLMOperations
from tests.llm.configuration_fixtures import rag_settings, rag_project

import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from llm.core.results import ExecutionResult
from llm.core.models import Run, RunStatus, new_id
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
        await self.project.agents.acreate({"engine": "test"}, identifier="test")
        self.session = await self.project.sessions.acreate("Requests", agent_ids=sorted(await self.project.agents.alist()))

    async def test_request_result_loads_run_once_and_never_caches_state(self):
        request = await self.session.run.submit("hello", recipients=["test"])
        run = (await request.wait())[0]
        await self.session.run.shutdown()
        repository = self.app.run_repository
        with patch.object(repository, "load", wraps=repository.load) as load:
            result = (await request.aresults())[0]
            self.assertEqual(result.run_id, run.id)
            self.assertEqual(load.call_count, 1)
        changed = await run.aget_data()
        changed.error = "updated observation"
        with self.app.project_manager.ownership.scope():
            repository.save(changed)
        self.assertEqual(((await request.aresults())[0]).error, "updated observation")

    async def test_response_loads_parent_session_once(self):
        run = (await (await self.session.run.submit("hello", recipients=["test"])).wait())[0]
        await self.session.run.shutdown()
        repository = self.app.project_manager.sessions.repository
        with patch.object(repository, "load", wraps=repository.load) as load:
            self.assertEqual((await run.aresponse()).content, "answer")
            self.assertEqual(load.call_count, 1)

    async def test_result_page_converts_only_selected_runs(self):
        runs = []
        for text in ("one", "two", "three"):
            runs.append((await (await self.session.run.submit(text, recipients=["test"])).wait())[0])
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

    async def test_warm_result_page_reads_only_selected_runs_and_invalidates_changes(self):
        from llm.services.infrastructure import storage
        repository = self.app.run_repository
        sessions = [await self.session.aget_data(), await (await self.project.sessions.acreate(agent_ids=sorted(await self.project.agents.alist()))).aget_data()]
        runs = []
        with self.app.project_manager.ownership.scope():
            for index in range(12):
                session = sessions[index % 2]
                identifier = new_id()
                run = Run(identifier, session.id, new_id(), new_id(), "test", repository.paths(session, identifier), agent_id="fixture", metadata={"agent_binding": AgentBinding.create(session.project_id, "fixture", {"engine": 'test'}).to_dict()})
                run.status = RunStatus.COMPLETED if index % 3 else RunStatus.FAILED
                run.started_at = run.ended_at = f"2026-10-16T00:00:{index:02d}+00:00"
                repository.create(run)
                runs.append(run)
        read_json = storage.read_json
        def read_count(mock):
            return sum(call.args[0].name == "run.json" for call in mock.call_args_list)
        query = Query(status="completed", descending=True, offset=1, limit=2)
        with patch.object(storage, "read_json", wraps=read_json) as read:
            self.assertEqual(len(await self.project.results.alist()), len(runs))
            self.assertEqual(read_count(read), len(runs))  # 전체 결과는 별도 투영 생성 없이 한 번씩만 읽는다.
            read.reset_mock()
            cold = await self.project.results.alist(query=query)
            self.assertEqual(read_count(read), len(runs) + 2)
            read.reset_mock()
            warm = await self.project.results.alist(query=query)
            self.assertEqual(warm, cold)
            self.assertEqual(read_count(read), 2)
        changed = runs[0]
        changed.status, changed.ended_at = RunStatus.COMPLETED, "2026-10-16T01:00:00+00:00"
        with self.app.project_manager.ownership.scope():
            storage.atomic_domain_json(changed.paths.root / "run.json", storage.record(changed))
        with patch.object(storage, "read_json", wraps=read_json) as read:
            page = await self.project.results.alist(query=Query(descending=True, limit=1))
            self.assertEqual([row.run_id for row in page], [changed.id])
            self.assertEqual(read_count(read), 2)  # 바뀐 투영 한 개 + 선택한 원본 한 개
            read.reset_mock()
            self.assertEqual(await self.project.results.alist(query=Query(limit=0)), [])
            self.assertEqual(read_count(read), 0)
            with self.assertRaisesRegex(ValueError, "cursor"):
                await self.project.results.alist(query=Query(after="absent", limit=0))
        ordered = sorted(runs, key=lambda r: (r.ended_at or r.started_at or "", r.id))
        for descending in (False, True):
            for status in (None, "completed", "failed"):
                selected = Query(after=runs[3].id, status=status, descending=descending, offset=1, limit=2)
                page = await self.project.results.alist(query=selected)
                self.assertEqual([r.run_id for r in page], [r.id for r in selected.apply(ordered)])
        # 삭제된 원본은 캐시가 남아 있어도 다시 목록에 등장하지 않는다.
        with self.app.project_manager.ownership.scope():
            repository.delete_history(sessions[0], changed.id)
        self.assertNotIn(changed.id, [r.run_id for r in await self.project.results.alist()])
        # 페이지 밖에 있는 파일의 변경/소유권 오류도 캐시가 숨기지 않는다.
        from llm.services.infrastructure.storage import atomic_domain_json, record
        invalid = runs[1]
        atomic_domain_json(invalid.paths.root / "run.json", {**record(invalid), "session_id": "wrong-owner"})
        with self.assertRaisesRegex(ValueError, "ownership"):
            await self.project.results.alist(query=Query(limit=1))


@unittest.skipUnless(all(importlib.util.find_spec(n) for n in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class RAGReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from llm.components.rag import RAGComponent, EmbeddingModel
        from tests.llm.test_rag_components import Extractor, fake_embedding
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.component = RAGComponent(embedding=EmbeddingModel(model="test/embedding", adapter=LiteLLMOperations(call=fake_embedding)),
                                      extractor=Extractor())
        self.app = LargeLanguageModel(Path(temp.name), engines={}, components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("RAG", components=["rag"], config=rag_project())
        self.data = await self.project.components.aget("rag")
        await self.data.aadd_document(identifier="manual", title="Manual", content="Alice owns Atlas")

    async def test_reranked_query_reads_corpus_and_searches_once(self):
        from llm.components.rag import corpus_storage as implementation
        self.component.reranker = SimpleNamespace(rerank=AsyncMock(return_value=RerankResult((RankedDocument(0, 0.9),))))
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
            return RerankResult((RankedDocument(0, 1.0),))
        self.component.reranker = SimpleNamespace(rerank=change)
        for method in (self.data.asearch, self.data.asearch_documents):
            with patch.object(self.component, "search", wraps=self.component.with_config(self.project.data).search) as search:
                with self.assertRaises(RAGConflictError):
                    await method("Alice", method="bm25", rerank=True)
                self.assertEqual(search.call_count, 1)

    async def test_reranking_rechecks_component_selection(self):
        async def remove(*args):
            await self.project.components.aremove("rag")
            return RerankResult((RankedDocument(0, 1.0),))
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
            hit = {**chunks[0], "title": doc["title"], "generation": doc["generation"], "revision": 1, "metadata": {}}
            relations = [{"document_id": "manual", "source_id": f"manual:c{i}"} for i in range(1, 100)]
            with patch("llm.components.rag.component.related_graph", return_value={"entities": [], "relations": relations}):
                result = self.component.combined_result(project, snapshot, "Alice", [hit], max_hops=2, relation_limit=100)
        self.assertEqual(len(result["sources"]), 100)
        self.assertTrue(all(source["generation"] == doc["generation"] for source in result["sources"]))
        self.assertEqual(chunks.iterations, 1)

    async def test_compaction_does_not_delete_generations_when_active_corpus_is_unreadable(self):
        plan = await self.data.acompaction_plan()
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
                await self.data.acompact([], expected_version=plan["version"])
            self.assertTrue(inactive.is_dir())
        finally:
            corpus.write_bytes(original)
