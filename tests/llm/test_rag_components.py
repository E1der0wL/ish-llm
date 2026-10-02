"""Component 공개 API로 실제 Chroma/Kuzu CRUD·검색·수명·실패 원자성을 검증한다."""
from tests.llm.configuration_fixtures import rag_settings, rag_project

import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.rag.component import RAGConflictError
from llm.components.rag.splitting import split_markdown
from llm.components.rag import TripleExtractor
from llm.llm import LargeLanguageModel


async def fake_embedding(**request):
    # 외부 API 없는 검증 전용 벡터. 검색/수정/삭제에는 실제 로컬 DB를 사용한다.
    return SimpleNamespace(data=[{"index": i, "embedding": [
        1.0 + text.count("backup"), 1.0 + text.count("Alice"), 1.0 + text.count("Seoul")
    ]} for i, text in enumerate(request["input"])])


class Extractor:
    async def extract(self, chunks):
        matches = [c for c in chunks if "Alice owns Atlas" in c["text"]]
        return {"entities": [{"id": "alice", "name": "Alice"}, {"id": "atlas", "name": "Atlas"}],
                "relations": [{"source": "alice", "target": "atlas", "type": "owns",
                               "source_id": c["id"], "evidence": "Alice owns Atlas"} for c in matches]}


class ParsingTests(unittest.TestCase):
    def test_nested_sections_fences_long_paragraphs_and_no_heading(self):
        content = "intro\n\n## Parent\n\nparent body\n\n### Child\n\n```python\n# not a heading\n```\n\n" + "x" * 150
        doc = split_markdown(content, "doc", chunk_size=64)
        self.assertEqual(len(doc["sections"]), 3)
        self.assertIn("### Child", doc["sections"]["doc:s2"]["text"])
        self.assertEqual(doc["sections"]["doc:s3"]["heading_path"], ["Parent", "Child"])
        self.assertTrue(all(len(c["text"]) <= 64 for c in doc["chunks"]))
        self.assertEqual(split_markdown("plain text", "d", chunk_size=2000)["chunks"][0]["text"], "plain text")

    def test_rag_model_import_is_lightweight(self):
        result = subprocess.run([sys.executable, "-c", "import sys; from llm.components.rag import EmbeddingModel; "
            "assert not any(n in sys.modules for n in ('litellm','chromadb','kuzu','llm.services','llm.core'))"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_litellm_completion_contract(self):
        call = AsyncMock(return_value={"choices": [{"message": {"content": '{"entities": [], "relations": []}'}}]})
        extractor = TripleExtractor(model="test", completion_fn=call)
        self.assertEqual(await extractor.extract([{"id": "d:c1", "text": "untrusted"}]),
                         {"entities": [], "relations": []})
        request = call.call_args.kwargs
        self.assertFalse(request["stream"])
        self.assertNotIn("response_format", request)
        self.assertIn("untrusted", request["messages"][-1]["content"])


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class RAGTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_during_lease_acquisition_does_not_leak_lock(self):
        from llm.components.rag.jobs import RAGJobs
        started, release = asyncio.Event(), asyncio.Event()
        closed = []
        async def acquire(*args):
            started.set()
            await release.wait()
            return SimpleNamespace(close=lambda: closed.append(True))
        jobs = RAGJobs(SimpleNamespace(ownership=None, _async_call=acquire))
        worker = asyncio.create_task(jobs.run('job'))
        await started.wait()
        worker.cancel()
        await asyncio.sleep(0)
        worker.cancel()
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await worker
        self.assertEqual(closed, [True])

    async def test_bm25_cache_reuses_generation_and_invalidates_on_document_update(self):
        from rank_bm25 import BM25Okapi
        await self.add(identifier='manual')
        with patch('rank_bm25.BM25Okapi', wraps=BM25Okapi) as build:
            await self.rag.asearch_documents('backup', method='bm25')
            await self.rag.asearch_documents('days', method='bm25')
            self.assertEqual(build.call_count, 1)
            await self.rag.aupdate_document('manual', content='Seoul deployment')
            self.assertEqual(await self.rag.asearch_documents('backup', method='bm25'), [])
            self.assertEqual(build.call_count, 2)
        self.assertLessEqual(self.rag_component.search_cache.used, self.project.data.config.component_configurations["rag"]["search_cache_chars"])

    async def test_reflink_fallback_copy_does_not_mutate_original(self):
        import errno
        from llm.components.rag.files import copy_file
        source, destination = self.root / 'source', self.root / 'destination'
        source.write_bytes(b'original')
        with patch('fcntl.ioctl', side_effect=OSError(errno.EOPNOTSUPP, 'unsupported')):
            copy_file(source, destination)
        destination.write_bytes(b'changed')
        self.assertEqual(source.read_bytes(), b'original')

    async def test_ingestion_retry_reuses_batches_and_os_lease(self):
        await self.rag.aconfigure(rag_settings({"embedding_concurrency": 1, "extraction_batch_size": 1}))
        content = '\n\n'.join(f'## Section {i}\n\nAlice owns Atlas {i}' for i in range(4))
        job = await self.rag.aenqueue_document(title='Manual', content=content, identifier='batches')
        calls = []
        async def embed(texts, **options):
            calls.append(texts)
            if len(calls) == 2:
                raise ConnectionError('embedding interrupted')
            return await fake_embedding(input=texts)
        with patch.object(self.embed, 'embed', embed):
            with self.assertRaises(ConnectionError):
                await self.rag.arun_job(job['id'])
            await self.rag.arun_job(job['id'], retry=True)
        self.assertEqual(sum(batch == calls[0] for batch in calls), 1)
        self.assertTrue(await self.rag.asearch('Alice', method='bm25'))
        self.assertFalse((self.project.paths.root / 'rag' / 'jobs' / job['id'] / 'batches').exists())

    async def test_ingestion_graph_failure_reuses_embeddings_and_completed_graph_batches(self):
        await self.rag.aconfigure(rag_settings({"extraction_batch_size": 1}))
        content = '\n\n'.join(f'## Part {i}\n\nAlice owns Atlas {i}' for i in range(3))
        job = await self.rag.aenqueue_document(title='Manual', content=content)
        calls = []
        async def extract(chunks):
            calls.append(chunks)
            if len(calls) == 2:
                raise ConnectionError('extract interrupted')
            return await Extractor().extract(chunks)
        with patch.object(self.rag_component.extractor, 'extract', extract):
            with self.assertRaises(ConnectionError):
                await self.rag.arun_job(job['id'])
            with patch.object(self.embed, 'embed', AsyncMock(side_effect=AssertionError('already embedded'))):
                await self.rag.arun_job(job['id'], retry=True)
        self.assertEqual(sum(batch == calls[0] for batch in calls), 1)
        graph = await self.rag.agraph_search('Alice')
        self.assertEqual(len(graph['relations']), 3)

    async def test_job_os_lease_blocks_live_worker_and_releases_after_cancellation(self):
        from llm.components.rag.jobs import RAGJobs
        jobs = RAGJobs(self.rag)
        job = await self.rag.aenqueue_document(title='Manual', content='Alice owns Atlas')
        lease = await self.rag._async_call(jobs._lease, job['id'])
        await self.rag._async_call(jobs._claim, job['id'], False)
        try:
            with self.assertRaisesRegex(ValueError, 'still alive'):
                await self.rag.arun_job(job['id'], retry=True)
        finally:
            lease.close()
        # 같은 PID가 살아 있어도 실제 워커 잠금이 해제되었으면 명시적 복구 가능하다.
        result = await self.rag.arun_job(job['id'], retry=True)
        self.assertEqual(result['status'], 'completed')

    async def test_cancelled_ingestion_never_publishes_and_releases_lease(self):
        from llm.components.rag.jobs import RAGJobs
        job = await self.rag.aenqueue_document(title='Manual', content='Alice owns Atlas')
        entered = asyncio.Event()
        async def embed(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.embed, 'embed', embed):
            worker = asyncio.create_task(self.rag.arun_job(job['id']))
            await asyncio.wait_for(entered.wait(), 5)
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker
        self.assertEqual((await self.rag.ajob(job['id']))['status'], 'cancelled')
        self.assertEqual(await self.rag.alist_documents(), [])
        lease = await self.rag._async_call(RAGJobs(self.rag)._lease, job['id'])
        lease.close()

    async def test_ingestion_retry_reuses_prepared_document_after_reopen(self):
        job = await self.rag.aenqueue_document(title='Manual', content='Alice owns Atlas', identifier='retry-doc')
        with patch.object(self.rag_component, '_build', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                await self.rag.arun_job(job['id'])
        self.assertEqual((await self.rag.ajob(job['id']))['status'], 'failed')
        project_id = self.project.id
        await self.app.shutdown()
        app = LargeLanguageModel(self.root, engines={}, components=[self.rag_component])
        self.addAsyncCleanup(app.shutdown)
        rag = (await app.projects.aload(project_id)).components.rag
        with patch.object(self.embed, 'embed', AsyncMock(side_effect=AssertionError('must reuse prepared vectors'))):
            result = await rag.arun_job(job['id'], retry=True)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(await rag.asearch('Alice', method='bm25'))

    async def test_durable_ingestion_queue_and_cancel(self):
        job = await self.rag.aenqueue_document(title='Manual', content='Alice owns Atlas', identifier='job-doc')
        self.assertEqual((await self.rag.ajob(job['id']))['status'], 'queued')
        results = await self.rag.arun_jobs()
        self.assertEqual(results[0]['status'], 'completed')
        self.assertTrue(await self.rag.asearch('Alice', method='bm25'))
        cancelled = await self.rag.aenqueue_document(title='Cancel', content='not indexed')
        await self.rag.acancel_job(cancelled['id'])
        with self.assertRaises(ValueError):
            await self.rag.arun_job(cancelled['id'])

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.embed = EmbeddingModel(model="test/embedding", embedding_fn=fake_embedding)
        self.rag_component = RAGComponent(embedding=self.embed, extractor=Extractor())
        self.graph_component = self.rag_component
        self.app = LargeLanguageModel(self.root, engines={}, components=[self.rag_component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("test", components=["rag"], config=rag_project({"component_configurations": {"rag": {"chunk_size": 256}}}))
        self.rag = await self.project.components.aget("rag")
        self.graph = await self.project.components.aget("rag")

    async def add(self, handle=None, **kwargs):
        return await (handle or self.rag).aadd_document(title="Manual", content="## Backup\n\nbackup every 15 minutes.\n\nKeep 30 days.", **kwargs)

    async def test_crud_search_expansion_and_no_old_chunks(self):
        doc = await self.add(identifier="manual", metadata={"source": "manual.md"})
        self.assertEqual(doc["revision"], 1)
        self.assertNotIn("vectors", doc)
        self.assertEqual(len(await self.rag.alist_documents()), 1)
        for method in ("bm25", "vector", "hybrid"):
            hits = await self.rag.asearch_documents("backup", method=method, expand="section", limit=1)
            self.assertEqual(hits[0]["document_id"], "manual")
            self.assertIn("Keep 30 days", hits[0]["context"])
        self.assertEqual((await self.rag.asearch_documents("backup", expand="document", limit=1))[0]["context"], doc["content"])
        updated = await self.rag.aupdate_document("manual", content="## New\n\nSeoul deployment.", expected_revision=1)
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(await self.rag.asearch_documents("backup", method="bm25"), [])
        with self.assertRaises(RAGConflictError):
            await self.rag.adelete_document("manual", expected_revision=1)
        await self.rag.adelete_document("manual", expected_revision=2)
        self.assertEqual(await self.rag.asearch_documents("Seoul"), [])
        self.assertEqual(await self.rag.alist_documents(), [])
        self.assertEqual(len(list((self.project.paths.root / "rag" / "generations").iterdir())), 1)

    async def test_graph_shared_evidence_deleted_only_for_own_document(self):
        for identifier in ("one", "two"):
            await self.graph.aadd_document(title=identifier, content="Alice owns Atlas", identifier=identifier)
        graph = await self.graph.agraph_search("Alice")
        self.assertEqual(len(graph["entities"]), 2)
        self.assertEqual({r["document_id"] for r in graph["relations"]}, {"one", "two"})
        await self.graph.adelete_document("one")
        graph = await self.graph.agraph_search("Alice")
        self.assertEqual([r["document_id"] for r in graph["relations"]], ["two"])
        self.assertEqual((await self.graph.asearch_documents("Alice", limit=1))[0]["document_id"], "two")
        await self.graph.aupdate_document("two", content="Atlas is archived")
        self.assertEqual((await self.graph.agraph_search("Alice"))["relations"], [])
        self.assertEqual(await self.graph.asearch_documents("owns", method="bm25"), [])

    async def test_provider_and_build_failure_keep_previous_generation(self):
        await self.add(identifier="doc")
        with patch.object(self.embed, "embed", AsyncMock(side_effect=RuntimeError("provider down"))):
            with self.assertRaisesRegex(RuntimeError, "provider down"):
                await self.rag.aupdate_document("doc", content="replacement")
        with patch.object(self.rag_component, "_build", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                await self.rag.aupdate_document("doc", content="replacement")
        self.assertEqual((await self.rag.aget_document("doc"))["revision"], 1)
        self.assertTrue(await self.rag.asearch_documents("backup"))

    async def test_invalid_extraction_does_not_publish(self):
        invalid = {"entities": [{"id": "a", "name": "Alice"}], "relations": [
            {"source": "a", "target": "a", "type": "owns", "source_id": "doc:c1", "evidence": "invented"}]}
        with patch.object(self.graph_component.extractor, "extract", AsyncMock(return_value=invalid)):
            with self.assertRaisesRegex(ValueError, "quotation"):
                await self.graph.aadd_document(title="bad", content="Alice", identifier="doc")
        self.assertEqual(await self.graph.alist_documents(), [])

    async def test_publication_error_after_replace_never_deletes_active_database(self):
        from llm.services.infrastructure.storage import atomic_json
        await self.add(identifier="doc")
        def uncertain_write(path, value):
            atomic_json(path, value)
            if path.name == "active.json":
                raise OSError("directory sync failed after replace")
        with patch("llm.components.rag.component.atomic_json", side_effect=uncertain_write):
            with self.assertRaisesRegex(OSError, "directory sync"):
                await self.rag.aupdate_document("doc", content="Seoul deployment")
        self.assertEqual((await self.rag.aget_document("doc"))["revision"], 1)
        self.assertTrue(await self.rag.asearch_documents("backup"))
        await self.rag.acompact()

    async def test_clone_isolated_and_reopen_without_provider(self):
        await self.graph.aadd_document(title="one", content="Alice owns Atlas", identifier="one")
        clone = await self.project.aclone()
        copied = await clone.components.aget("rag")
        await self.graph.adelete_document("one")
        self.assertTrue((await copied.agraph_search("Alice"))["relations"])
        self.assertTrue(await copied.asearch_documents("Alice", method="bm25"))
        project_id = clone.id
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, engines={}) as app:
            project = await app.projects.aload(project_id)
            reopened = await project.components.aget("rag")
            self.assertTrue((await reopened.agraph_search("Alice"))["relations"])
            self.assertTrue(await reopened.asearch_documents("Alice", method="bm25"))
            with self.assertRaisesRegex(ValueError, "Configure"):
                await reopened.asearch_documents("Alice", method="vector")
        # 프로세스에도 모델이나 키를 전달하지 않고 DB를 다시 연다.
        code = "\n".join(["import asyncio", "from llm.llm import LargeLanguageModel", "async def run():",
            " async with LargeLanguageModel(" + repr(str(self.root)) + ", engines={}) as app:",
            "  project = await app.projects.aload(" + repr(project_id) + ")",
            "  graph = await project.components.aget('rag')",
            "  assert (await graph.agraph_search('Alice'))['relations']", "asyncio.run(run())"])
        process = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code], capture_output=True, text=True, timeout=45)
        self.assertEqual(process.returncode, 0, process.stderr)

    async def test_project_and_component_lifecycle_guards(self):
        await self.add(identifier="doc")
        other = await self.app.projects.acreate("other", components=["rag"], config=rag_project())
        empty = await other.components.aget("rag")
        self.assertEqual(await empty.asearch_documents("backup"), [])
        await self.project.components.aremove("rag")
        with self.assertRaisesRegex(ValueError, "not enabled"):
            await self.rag.asearch_documents("backup", method="bm25")
        await self.project.components.aselect(["rag"])
        self.assertTrue(await self.rag.asearch_documents("backup", method="bm25"))
        await self.project.adelete()
        with self.assertRaisesRegex(ValueError, "deleted"):
            await self.rag.alist_documents()

    async def test_cancellation_during_embedding_does_not_publish(self):
        entered = asyncio.Event()
        async def block(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.embed, "embed", block):
            pending = asyncio.create_task(self.add())
            await entered.wait()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertEqual(await self.rag.alist_documents(), [])

    async def test_concurrent_preparation_conflict_never_overwrites(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def block(**request):
            entered.set()
            await release.wait()
            return await fake_embedding(**request)
        with patch.object(self.embed, "_call_fn", block):
            first = asyncio.create_task(self.add(identifier="slow"))
            await entered.wait()
        await self.add(identifier="fast")
        release.set()
        with self.assertRaises(RAGConflictError):
            await first
        self.assertEqual([d["id"] for d in await self.rag.alist_documents()], ["fast"])

    async def test_removed_recreated_component_rejects_prepared_write(self):
        entered, release = asyncio.Event(), asyncio.Event()
        component = self.rag_component
        original = component.configured(self.project.data).prepare
        async def block(*args, **kwargs):
            document = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return document
        # 모델 호출 예약이 완료된 이후에도 준비/공개 사이의 세대 변경은 거부해야 한다.
        with patch.object(component, "prepare", block):
            pending = asyncio.create_task(self.add())
            await entered.wait()
            await self.project.components.aremove("rag", permanent=True)
            await self.project.components.aselect(["rag"])
            release.set()
            with self.assertRaises(RAGConflictError):
                await pending
        self.assertEqual(await self.rag.alist_documents(), [])

    async def test_invalid_vectors_model_mismatch_and_paths(self):
        with patch.object(self.embed, "embed", AsyncMock(return_value={"data": [{"index": 0, "embedding": [float("nan")]}]})):
            with self.assertRaises(ValueError):
                await self.rag.aadd_document(title="bad", content="bad")
        with self.assertRaises(ValueError):
            await self.add(identifier="../escape")
        await self.add(identifier="doc")
        self.embed.params["model"] = "different"
        with self.assertRaisesRegex(ValueError, "model differs"):
            await self.rag.asearch_documents("backup")
        self.assertTrue(await self.rag.asearch_documents("backup", method="bm25"))

    async def test_generic_definition_crud_does_not_change_indexed_documents(self):
        await self.rag.acreate({"arbitrary": True}, identifier="definition")
        await self.add(identifier="doc")
        await self.rag.adelete("definition")
        self.assertTrue(await self.rag.asearch_documents("backup"))
        with self.assertRaises(FileExistsError):
            await self.add(identifier="doc")

    async def test_rerank_and_query_validation(self):
        from llm.components.rag import RerankModel
        await self.add(identifier="doc")
        call = AsyncMock(return_value={"results": [{"index": 1, "relevance_score": 0.9}]})
        self.rag_component.reranker = RerankModel(model="test", rerank_fn=call)
        original = await self.rag.asearch_documents("backup", limit=3)
        ranked = await self.rag.asearch_documents("backup", limit=3, rerank=True)
        self.assertEqual(ranked[0]["id"], original[1]["id"])
        self.assertEqual(ranked[0]["rerank_score"], 0.9)
        for kwargs in ({"limit": 0}, {"method": "bad"}, {"expand": "bad"}):
            with self.assertRaises(ValueError):
                await self.rag.asearch_documents("backup", **kwargs)
        with self.assertRaises(ValueError):
            await self.graph.agraph_search("Alice", max_hops=0)

    async def test_graph_two_hops_cycles_limits_and_bound_parameters(self):
        text = "Alice owns Atlas. Atlas runs in Seoul. Seoul hosts Alice."
        graph = {"entities": [{"id": name, "name": name} for name in ("Alice", "Atlas", "Seoul")],
                 "relations": [{"source": source, "target": target, "type": kind,
                                "source_id": "doc:c1", "evidence": quote}
                               for source, target, kind, quote in (
                                   ("Alice", "Atlas", "owns", "Alice owns Atlas"),
                                   ("Atlas", "Seoul", "runs_in", "Atlas runs in Seoul"),
                                   ("Seoul", "Alice", "hosts", "Seoul hosts Alice"))]}
        with patch.object(self.graph_component.extractor, "extract", AsyncMock(return_value=graph)):
            await self.graph.aadd_document(title="Cycle", content=text, identifier="doc")
        result = await self.graph.agraph_search("Alice", max_hops=2)
        self.assertEqual([r["target"] for r in result["relations"]], ["Atlas", "Seoul"])
        self.assertEqual(len((await self.graph.agraph_search("Alice", max_hops=5))["relations"]), 3)
        self.assertEqual(len((await self.graph.agraph_search("Alice", limit=1))["relations"]), 1)
        self.assertEqual((await self.graph.agraph_search("Alice' RETURN e //"))["relations"], [])

    async def test_cancelled_commit_drains_and_shutdown_rejects_model_result(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        original = self.rag_component._build
        def build(path, documents):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test release")
            return original(path, documents)
        with patch.object(self.rag_component, "_build", build):
            pending = asyncio.create_task(self.add(identifier="doc"))
            self.assertTrue(await asyncio.to_thread(entered.wait, 10))
            pending.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertTrue(await self.rag.asearch_documents("backup"))
        provider_entered, provider_release = asyncio.Event(), asyncio.Event()
        async def block(**request):
            provider_entered.set()
            await provider_release.wait()
            return await fake_embedding(**request)
        with patch.object(self.embed, "_call_fn", block):
            pending = asyncio.create_task(self.add(identifier="late"))
            await provider_entered.wait()
            await self.app.shutdown()
            provider_release.set()
            with self.assertRaisesRegex(RuntimeError, "shut"):
                await pending

    async def test_retrieval_engine_records_steps_in_owning_run(self):
        from llm.engines.base import BaseEngine
        await self.add(identifier="doc")
        async def retrieve(context):
            project = await self.app.projects.aload(context.project.id)
            rag = await project.components.aget("rag")
            yield json.dumps(await rag.asearch_documents("backup"))
        self.app.engines.register("retrieve", BaseEngine("Search", kind="retrieval", action=retrieve))
        session = await self.project.sessions.acreate("Question")
        request = await session.run.submit("backup", engine="retrieve")
        run = await request.wait(timeout=20)
        self.assertEqual(str((await run.aresult()).status), "completed")
        self.assertIn("15 minutes", (await run.aresponse()).content)
        steps = await run.steps.alist()
        self.assertEqual([step.kind for step in steps], ["retrieval"])
        self.assertEqual([str(step.status) for step in steps], ["completed"])
