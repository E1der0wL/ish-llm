"""실제 Chroma/Kuzu 공개·재개 경계와 저사양 청크 병렬 처리를 확인한다."""
from tests.llm.configuration_fixtures import rag_settings, rag_project

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from llm.components.rag import RAGComponent, EmbeddingModel, TripleExtractor
from llm.components.rag.ingestion import prepare_vectors, VectorCache
from llm.llm import LargeLanguageModel, ProjectConfig
from llm.providers.requests import ProviderError


async def embedding(**request):
    return {"data": [{"index": i, "embedding": [float(len(text)), 1.0]} for i, text in enumerate(request["input"])]}


async def extract(**request):
    return {"choices": [{"message": {"content": '{"entities":[],"relations":[]}'}}]}


class IngestionTests(unittest.IsolatedAsyncioTestCase):
    def component(self, call=embedding):
        from types import SimpleNamespace
        return RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=call),
                            extractor=TripleExtractor(model="test", completion_fn=extract)).configured(
                                SimpleNamespace(id="fixture", config=rag_project(), paths=SimpleNamespace(root=Path(tempfile.gettempdir()) / "ish-rag-test-fixture")))

    async def test_bounded_workers_order_and_admission(self):
        from llm.providers.calls import ProviderCalls, ProviderLimits
        from contextlib import nullcontext
        for concurrency, limit in ((1, None), (2, None), (4, None), (4, 2)):
            with self.subTest(concurrency=concurrency, limit=limit):
                active = peak = worker_peak = 0
                calls, finished = [], []
                async def delayed(**request):
                    nonlocal active, peak, worker_peak
                    self.assertEqual(len(request["input"]), 1)
                    position = int(request["input"][0])
                    calls.append(position)
                    active += 1
                    peak = max(peak, active)
                    worker_peak = max(worker_peak, sum(t.get_name().startswith("rag-embedding-") for t in asyncio.all_tasks()))
                    try:
                        await asyncio.sleep(.002 * (10 - position))
                        finished.append(position)
                        # index가 없거나 모두 0이어도 위치는 호출자가 소유한다.
                        return {"data": [{"embedding": [position + 1., 1.], **({"index": 0} if position % 2 else {})}]}
                    finally:
                        active -= 1
                component = self.component(delayed)
                component.embedding_concurrency = concurrency
                doc = {"chunks": [{"text": str(i)} for i in range(10)]}
                admission = ProviderCalls(ProviderLimits(max_active=limit)) if limit else None
                with admission.scope() if admission else nullcontext():
                    await prepare_vectors(component, doc)
                self.assertEqual([v[0] for v in doc["vectors"]], list(range(1, 11)))
                self.assertEqual(peak, min(concurrency, limit or concurrency))
                self.assertLessEqual(worker_peak, concurrency)
                self.assertEqual(sorted(calls), list(range(10)))
                if admission:
                    self.assertEqual(admission.stats, {"active": 0, "waiting": 0})

    async def test_completely_reverse_completion_and_checkpoint_numbering(self):
        ready = [asyncio.Event() for _ in range(10)]
        entered, completed, saved = [], [], {}
        async def reverse(**request):
            position = int(request["input"][0])
            entered.append(position)
            if len(entered) == 10:
                ready[9].set()
            await ready[position].wait()
            completed.append(position)
            if position:
                ready[position - 1].set()
            return {"data": [{"index": 0, "embedding": [position + 1.]}]}
        async def progress(key, signature, value=None):
            if value is not None:
                saved[key] = value
            return saved.get(key)
        component = self.component(reverse)
        component.embedding_concurrency = 10
        document = {"chunks": [{"text": str(i)} for i in range(10)]}
        await asyncio.wait_for(prepare_vectors(component, document, progress=progress), 5)
        self.assertEqual(completed, list(reversed(range(10))))
        for position in range(10):
            self.assertEqual(saved[f"embedding_{position}"]["vector"], [position + 1.])
        self.assertEqual(document["vectors"], [[i + 1.] for i in range(10)])

    async def test_cancel_keeps_completed_checkpoints_and_stops_workers(self):
        saved, calls = {}, []
        checkpointed, entered = asyncio.Event(), asyncio.Event()
        async def block(**request):
            text = request["input"][0]
            calls.append(text)
            if text != "0":
                entered.set()
                await asyncio.Event().wait()
            return await embedding(**request)
        async def progress(key, signature, value=None):
            if value is not None:
                saved[key] = value
                checkpointed.set()
            return saved.get(key)
        component = self.component(block)
        document = {"chunks": [{"text": str(i)} for i in range(1000)]}
        task = asyncio.create_task(prepare_vectors(component, document, progress=progress))
        await asyncio.wait_for(checkpointed.wait(), 5)
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(set(saved), {"embedding_0"})
        self.assertLessEqual(len(calls), 3)
        self.assertNotIn("vectors", document)
        self.assertFalse(any(t.get_name().startswith("rag-embedding-") for t in asyncio.all_tasks()))

    async def test_corrupt_cache_checkpoint_and_corpus_dimension_fail_closed(self):
        from unittest.mock import patch
        for cached in ([0, 0], [float("nan"), 1], [1, 2, 3]):
            component = self.component()
            component._embedding_dimensions = 2
            with patch.object(component.vector_cache, "get", return_value=cached), self.assertRaises(ValueError):
                await prepare_vectors(component, {"chunks": [{"text": "one"}]})
        async def corrupt(*args):
            return {"fingerprint": "wrong", "text_hash": "wrong", "vector": [1, 2]}
        with self.assertRaisesRegex(ValueError, "identity"):
            await prepare_vectors(self.component(), {"chunks": [{"text": "one"}]}, progress=corrupt)

    async def test_removed_batch_settings_and_concurrency_schema(self):
        component = self.component()
        for value in (0, 33, True, 2.5):
            with self.assertRaises(ValueError):
                component.validate_configuration({"embedding_concurrency": value})
        for key in ("embedding_batch_size", "embedding_batching"):
            with self.assertRaisesRegex(ValueError, "removed"):
                component.validate_configuration({key: 128})
        self.assertNotIn("default", component.configuration_schema()["properties"]["embedding_concurrency"])

    async def test_partial_content_cache_and_durable_unchanged_reuse(self):
        call = AsyncMock(side_effect=embedding)
        component = self.component(call)
        first = {"chunks": [{"text": "# Probe"}, {"text": "Probe belongs to Test Suite."}]}
        second = {"chunks": [{"text": "# Probe"}, {"text": "Probe belongs to Integration Suite."}]}
        await prepare_vectors(component, first)
        await prepare_vectors(component, second)
        self.assertEqual(call.call_args.kwargs["input"], ["Probe belongs to Integration Suite."])
        self.assertEqual(second["vectors"][0], first["vectors"][0])
        self.assertEqual(second["vectors"][1][0], len(second["chunks"][1]["text"]))
        first["profile"] = {"dimensions": 2}
        component.vector_cache = VectorCache()
        call.reset_mock()
        await prepare_vectors(component, second, previous=first)
        self.assertEqual(call.call_args.kwargs["input"], ["Probe belongs to Integration Suite."])
        component.vector_cache = VectorCache()
        component.embedding.params["dimensions"] = 2
        call.reset_mock()
        await prepare_vectors(component, second, previous=first)
        self.assertEqual(call.await_count, 2)
        self.assertTrue(all(len(c.kwargs["input"]) == 1 for c in call.call_args_list))

    async def test_original_position_map_repeated_text_and_cache(self):
        call = AsyncMock(side_effect=embedding)
        component = self.component(call)
        first = {"chunks": [{"text": "cached"}]}
        await prepare_vectors(component, first)
        second = {"chunks": [{"text": text} for text in ["new", "cached", "other", "new"]]}
        await prepare_vectors(component, second)
        self.assertEqual([c.kwargs["input"] for c in call.call_args_list], [["cached"], ["new"], ["other"]])
        self.assertEqual([v[0] for v in second["vectors"]], [3., 6., 5., 3.])
    async def test_previous_fingerprint_never_reuses_incompatible_vectors(self):
        from copy import deepcopy
        call = AsyncMock(side_effect=embedding)
        component = self.component(call)
        prior = {"chunks": [{"text": "text"}], "vectors": [[999., 999.]],
                 "embedding_fingerprint": "previous-configuration", "profile": {"dimensions": 2}}
        original = deepcopy(prior)
        current = {"chunks": deepcopy(prior["chunks"])}
        await prepare_vectors(component, current, previous=prior)
        self.assertEqual(call.await_count, 1)
        self.assertEqual(current["vectors"], [[4., 1.]])
        self.assertNotEqual(current["embedding_fingerprint"], prior["embedding_fingerprint"])
        self.assertEqual(prior, original)

    async def test_failed_chunk_resume_reuses_completed_ordinals(self):
        requests, saved = [], {}
        failed = True
        async def small(**request):
            text = request["input"][0]
            requests.append(text)
            await asyncio.sleep(.02 if text == "ccc" else .001)
            if failed and text == "ccc":
                raise TimeoutError()
            return await embedding(**request)
        component = self.component(small)
        component.embedding = component.embedding.with_provider({"max_attempts": 1})
        component.embedding_cache_max_bytes = 0
        async def progress(key, signature, value=None):
            if value is not None:
                saved[key] = (signature, value)
            entry = saved.get(key)
            return entry[1] if entry and entry[0] == signature else None
        doc = {"chunks": [{"text": text} for text in ["a", "bb", "ccc", "dddd"]]}
        with self.assertRaises(ProviderError):
            await prepare_vectors(component, doc, progress=progress)
        self.assertEqual(set(saved), {"embedding_0", "embedding_1", "embedding_3"})
        failed = False
        requests.clear()
        await prepare_vectors(component, doc, progress=progress)
        self.assertEqual(requests, ["ccc"])
        self.assertEqual([v[0] for v in doc["vectors"]], [1., 2., 3., 4.])
        self.assertEqual(doc["ingestion"]["checkpoint_reuse"], 3)

    async def test_single_chunk_failure_and_cross_chunk_dimensions_rejected(self):
        failure = AsyncMock(side_effect=TimeoutError())
        component = self.component(failure)
        component.embedding = component.embedding.with_provider({"max_attempts": 1})
        with self.assertRaises(ProviderError):
            await prepare_vectors(component, {"chunks": [{"text": "one"}]})
        self.assertEqual(failure.await_count, 1)
        call = AsyncMock(side_effect=[{"data": [{"index": 0, "embedding": [1, 1]}]},
                                     {"data": [{"index": 0, "embedding": [1, 1, 1]}]}])
        component = self.component(call)
        component.embedding_concurrency = 1
        with self.assertRaisesRegex(ValueError, "dimensions"):
            await prepare_vectors(component, {"chunks": [{"text": "one"}, {"text": "two"}]})


class PersistentRAGTests(unittest.IsolatedAsyncioTestCase):
    async def test_facade_admission_and_cancelled_job_preserves_checkpoints_and_active_generation(self):
        from llm.services.configuration import ServiceConfig
        from llm.providers.calls import ProviderLimits
        active = peak = 0
        block = False
        entered = asyncio.Event()
        async def provider(**request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                if block and request["input"][0] != "first":
                    entered.set()
                    await asyncio.Event().wait()
                await asyncio.sleep(.005)
                return await embedding(**request)
            finally:
                active -= 1
        with tempfile.TemporaryDirectory() as root:
            component = RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=provider))
            async with LargeLanguageModel(root, components=[component],
                    services=ServiceConfig(provider_limits=ProviderLimits(max_active=2))) as backend:
                project = await backend.projects.acreate("bounded", components=["rag"], config=rag_project(ProjectConfig(
                    component_configurations={"rag": {"embedding_concurrency": 4,
                        "extraction": {"failure_policy": "disabled"}}})))
                rag = await project.components.aget("rag")
                await rag.aadd_document(title="active", content="a\n\nb\n\nc\n\nd", identifier="active")
                self.assertEqual(peak, 2)
                _, before = await rag._async_call(rag._snapshot)
                block = True
                job = await rag.aenqueue_document(title="cancel", content="first\n\nsecond\n\nthird")
                task = asyncio.create_task(rag.arun_job(job["id"]))
                await asyncio.wait_for(entered.wait(), 5)
                path = project.paths.root / "rag" / "jobs" / job["id"] / "batches" / "embedding_0.json"
                async def checkpoint_exists():
                    while not await asyncio.to_thread(path.exists):
                        await asyncio.sleep(.005)
                await asyncio.wait_for(checkpoint_exists(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(path.exists())
                self.assertEqual((await rag.ajob(job["id"]))["status"], "cancelled")
                _, after = await rag._async_call(rag._snapshot)
                self.assertEqual(after["generation"], before["generation"])
                self.assertEqual(backend.provider_calls.stats, {"active": 0, "waiting": 0})
                self.assertEqual(active, 0)

    async def test_document_and_query_dimension_reject_without_publish(self):
        vector = [1., 2.]
        async def provider(**request):
            return {"data": [{"embedding": vector}]}
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[RAGComponent(
                    embedding=EmbeddingModel(model="test", embedding_fn=provider))]) as backend:
                project = await backend.projects.acreate("dimensions", components=["rag"], config=rag_project(ProjectConfig(
                    component_configurations={"rag": {"extraction": {"failure_policy": "disabled"}}})))
                rag = await project.components.aget("rag")
                await rag.aadd_document(title="first", content="first", identifier="first")
                _, before = await rag._async_call(rag._snapshot)
                vector = [1., 2., 3.]
                with self.assertRaisesRegex(ValueError, "dimensions"):
                    await rag.aupdate_document("first", content="different")
                with self.assertRaisesRegex(ValueError, "dimensions"):
                    await rag.asearch("query", method="vector")
                _, after = await rag._async_call(rag._snapshot)
                self.assertEqual(after["generation"], before["generation"])

    async def test_effective_retry_configuration_preserves_values_and_cache_contract(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[RAGComponent()]) as backend:
                values = {name: {"model": "test", "num_retries": 2, "max_retries": 3}
                          for name in ("embedding_params", "extraction_params", "rerank_params")}
                project = await backend.projects.acreate("settings", components=["rag"],
                    config=rag_project(ProjectConfig(component_configurations={"rag": values})))
                rag = await project.components.aget("rag")
                effective = rag.effective_configuration()["values"]
                for name in values:
                    self.assertEqual(effective[name]["num_retries"], 2)
                    self.assertEqual(effective[name]["max_retries"], 3)
                enforced = rag.effective_configuration()["enforced"]["embedding_params"]
                self.assertFalse(enforced["caching"])
                self.assertEqual(enforced["cache"], {"no-cache": True, "no-store": True})
                self.assertNotIn("caching", effective["rerank_params"])
                from llm.providers.requests import provider_schema
                self.assertEqual(set(provider_schema()["properties"]), {
                    "max_attempts", "wall_timeout", "delay_seconds", "max_delay_seconds"})
                self.assertFalse((Path(__file__).parents[2] / "llm/providers/openai.py").exists())

    async def test_partial_graph_and_disabled_without_extractor(self):
        async def partial(**request):
            chunks = json.loads(request["messages"][-1]["content"])
            if chunks[0]["text"] == "Unsupported paragraph.":
                return {"choices": [{"message": {"content": "invalid JSON"}}]}
            graph = {"entities": [{"id": "a", "name": "Atlas"}, {"id": "h", "name": "Harbor"}],
                     "relations": [{"source": "a", "target": "h", "type": "USES", "source_id": chunks[0]["id"],
                                    "evidence": "Atlas uses Harbor."}]}
            return {"choices": [{"message": {"content": json.dumps(graph)}}]}
        with tempfile.TemporaryDirectory() as root:
            component = RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=embedding),
                                    extractor=TripleExtractor(model="test", completion_fn=partial))
            async with LargeLanguageModel(root, components=[component], engines={}) as backend:
                project = await backend.projects.acreate("partial", components=["rag"], config=rag_project(ProjectConfig(
                    component_configurations={"rag": {"extraction_batch_size": 1,
                        "extraction": {"failure_policy": "best_effort", "repair_attempts": 0}}})))
                rag = await project.components.aget("rag")
                doc = await rag.aadd_document(title="test", content="Atlas uses Harbor.\n\nUnsupported paragraph.")
                result = await rag.agraph_search("Atlas")
                self.assertFalse(result["graph_complete"])
                self.assertEqual(len(result["relations"]), 1)
                self.assertFalse(doc["graph_complete"])
                component.extractor = None
                await rag.aconfigure(rag_settings({"extraction": {"failure_policy": "disabled"}}))
                doc = await rag.aadd_document(title="disabled", content="No extraction model.")
                self.assertFalse(doc["graph_complete"])

    async def test_graph_policies_and_capability_schema(self):
        for policy in ("required", "best_effort", "disabled"):
            with tempfile.TemporaryDirectory() as root:
                call = AsyncMock(return_value={"choices": [{"message": {"content": "broken JSON"}}]})
                component = RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=embedding),
                    extractor=TripleExtractor(model="test", completion_fn=call))
                async with LargeLanguageModel(root, components=[component], engines={}) as backend:
                    project = await backend.projects.acreate("test", components=["rag"], config=rag_project(ProjectConfig(
                        component_configurations={"rag": {"extraction": {"failure_policy": policy, "repair_attempts": 0}}})))
                    rag = await project.components.aget("rag")
                    if policy == "required":
                        with self.assertRaises(ValueError):
                            await rag.aadd_document(title="test", content="Atlas uses Harbor", identifier="doc")
                        self.assertEqual(await rag.alist_documents(), [])
                    else:
                        doc = await rag.aadd_document(title="test", content="Atlas uses Harbor", identifier="doc")
                        self.assertFalse(doc["graph_complete"])
                        result = await rag.asearch("Atlas", method="bm25")
                        self.assertTrue(result["documents"])
                        self.assertFalse(result["graph_complete"])
                        self.assertTrue(doc["graph_diagnostics"])
                        self.assertEqual(call.await_count, 0 if policy == "disabled" else 1)
                    from llm.components.rag.tools import search_tools
                    registry = search_tools(rag)
                    tool = registry.get("rag_search")
                    self.assertEqual(tool.parameters["properties"]["rerank"]["const"], False)
                    from llm.components.rag import RerankModel
                    component.reranker = RerankModel(model="test", rerank_fn=AsyncMock())
                    self.assertNotIn("const", search_tools(rag).get("rag_search").parameters["properties"]["rerank"])

    async def test_retry_reservation_cannot_exceed_project_quota(self):
        call = AsyncMock(side_effect=ConnectionResetError("PRIVATE payload"))
        with tempfile.TemporaryDirectory() as root:
            component = RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=call),
                                    extractor=TripleExtractor(model="test", completion_fn=extract))
            async with LargeLanguageModel(root, components=[component], engines={}) as backend:
                project = await backend.projects.acreate("quota", components=["rag"], config=rag_project(ProjectConfig(
                    policies={"usage": {"project_max_calls": 1}},
                    component_configurations={"rag": {"provider": {"max_attempts": 2, "delay_seconds": 0}}})))
                rag = await project.components.aget("rag")
                with self.assertRaisesRegex(Exception, "exhausted"):
                    await rag.aadd_document(title="test", content="secret text")
                self.assertEqual(call.await_count, 1)
                receipts = await rag.amodel_usage()
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0]["diagnostic_code"], "provider_connection")
                self.assertNotIn("PRIVATE", json.dumps(receipts))
                self.assertEqual(await rag.alist_documents(), [])

    async def test_job_failure_retry_reuses_chunk_and_cancellation_keeps_generation(self):
        calls = []
        should_fail = True
        async def unreliable(**request):
            calls.append(request["input"])
            if should_fail and "second" in request["input"][0]:
                raise ConnectionResetError()
            return await embedding(**request)
        with tempfile.TemporaryDirectory() as root:
            component = RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=unreliable),
                extractor=TripleExtractor(model="test", completion_fn=extract))
            async with LargeLanguageModel(root, components=[component], engines={}) as backend:
                project = await backend.projects.acreate("test", components=["rag"], config=rag_project(ProjectConfig(
                    component_configurations={"rag": {"embedding_concurrency": 1, "provider": {"max_attempts": 1},
                        "embedding_cache_max_bytes": 0}})))
                rag = await project.components.aget("rag")
                job = await rag.aenqueue_document(title="test", content="first\n\nsecond", identifier="doc")
                with self.assertRaises(ProviderError):
                    await rag.arun_job(job["id"])
                self.assertEqual(await rag.alist_documents(), [])
                state = await rag.ajob(job["id"])
                self.assertEqual(state["ingestion"]["chunks_completed"], 1)
                calls.clear()
                should_fail = False
                done = await rag.arun_job(job["id"], retry=True)
                self.assertEqual(done["status"], "completed")
                self.assertEqual(calls, [["second"]])
                before = await rag.aget_document("doc")
                entered = asyncio.Event()
                async def block(**request):
                    entered.set()
                    await asyncio.Event().wait()
                component.embedding._call_fn = block
                task = asyncio.create_task(rag.aupdate_document("doc", content="replacement"))
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(await rag.aget_document("doc"), before)
