"""실제 Chroma/Kuzu 공개·재개 경계와 저사양 배치 처리를 확인한다."""

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
        return RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=call),
                            extractor=TripleExtractor(model="test", completion_fn=extract))

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
        self.assertEqual(len(call.call_args.kwargs["input"]), 2)

    async def test_original_position_map_repeated_text_and_char_budget(self):
        call = AsyncMock(side_effect=embedding)
        component = self.component(call)
        first = {"chunks": [{"text": "cached"}]}
        await prepare_vectors(component, first)
        second = {"chunks": [{"text": text} for text in ["new", "cached", "other", "new"]]}
        await prepare_vectors(component, second)
        self.assertEqual(call.call_args.kwargs["input"], ["new", "other"])
        self.assertEqual([v[0] for v in second["vectors"]], [3., 6., 5., 3.])
        component.batching_options["max_batch_chars"] = 2
        with self.assertRaisesRegex(ValueError, "max_batch_chars"):
            await prepare_vectors(component, second)

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

    async def test_split_only_transient_and_checkpoint_children_reused(self):
        requests = []
        failed = True
        async def small(**request):
            texts = request["input"]
            requests.append(texts)
            if len(texts) > 2 or failed and texts == ["ccc", "dddd"]:
                raise TimeoutError()
            return await embedding(**request)
        component = self.component(small)
        component.embedding = component.embedding.with_provider({"max_attempts": 1})
        component.batching_options.update(max_split_depth=1, cache_max_bytes=0)
        saved = {}
        async def progress(key, signature, value=None):
            if value is not None:
                saved[key] = value
            return saved.get(key)
        doc = {"chunks": [{"text": text} for text in ["a", "bb", "ccc", "dddd"]]}
        with self.assertRaises(ProviderError):
            await prepare_vectors(component, doc, progress=progress)
        self.assertIn("embedding_0_l", saved)
        failed = False
        requests.clear()
        await prepare_vectors(component, doc, progress=progress)
        self.assertEqual(requests, [["ccc", "dddd"]])
        self.assertEqual([v[0] for v in doc["vectors"]], [1., 2., 3., 4.])
        corrupt = AsyncMock(return_value={"data": [{"index": 0, "embedding": [1, 1]}] * 4})
        component = self.component(corrupt)
        component.batching_options["max_split_depth"] = 5
        with self.assertRaises(ValueError):
            await prepare_vectors(component, {"chunks": doc["chunks"]})
        self.assertEqual(corrupt.await_count, 1)

    async def test_size_one_stops_and_cross_batch_dimensions_rejected(self):
        failure = AsyncMock(side_effect=TimeoutError())
        component = self.component(failure)
        component.embedding = component.embedding.with_provider({"max_attempts": 1})
        component.batching_options["max_split_depth"] = 8
        with self.assertRaises(ProviderError):
            await prepare_vectors(component, {"chunks": [{"text": "one"}]})
        self.assertEqual(failure.await_count, 1)
        call = AsyncMock(side_effect=[{"data": [{"index": 0, "embedding": [1, 1]}]},
                                     {"data": [{"index": 0, "embedding": [1, 1, 1]}]}])
        component = self.component(call)
        component.embedding_batch_size = 1
        with self.assertRaisesRegex(ValueError, "dimensions"):
            await prepare_vectors(component, {"chunks": [{"text": "one"}, {"text": "two"}]})


class PersistentRAGTests(unittest.IsolatedAsyncioTestCase):
    async def test_effective_retry_configuration_preserves_values_and_cache_contract(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[RAGComponent()]) as backend:
                values = {name: {"model": "test", "num_retries": 2, "max_retries": 3}
                          for name in ("embedding_params", "extraction_params", "rerank_params")}
                project = await backend.projects.acreate("settings", components=["rag"],
                    config=ProjectConfig(component_configurations={"rag": values}))
                rag = await project.components.aget("rag")
                effective = rag.effective_configuration()["values"]
                for name in values:
                    self.assertEqual(effective[name]["num_retries"], 2)
                    self.assertEqual(effective[name]["max_retries"], 3)
                self.assertFalse(effective["embedding_params"]["caching"])
                self.assertEqual(effective["embedding_params"]["cache"], {"no-cache": True, "no-store": True})
                self.assertNotIn("caching", effective["rerank_params"])
                from llm.providers.requests import provider_schema, provider_defaults
                self.assertEqual(set(provider_defaults()), {
                    "max_attempts", "wall_timeout", "delay_seconds", "max_delay_seconds"})
                self.assertEqual(set(provider_schema()["properties"]), set(provider_defaults()))
                self.assertFalse((Path(__file__).parents[1] / "providers/openai.py").exists())

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
                project = await backend.projects.acreate("partial", components=["rag"], config=ProjectConfig(
                    component_configurations={"rag": {"extraction_batch_size": 1,
                        "extraction": {"failure_policy": "best_effort", "repair_attempts": 0}}}))
                rag = await project.components.aget("rag")
                doc = await rag.aadd_document(title="test", content="Atlas uses Harbor.\n\nUnsupported paragraph.")
                result = await rag.agraph_search("Atlas")
                self.assertFalse(result["graph_complete"])
                self.assertEqual(len(result["relations"]), 1)
                self.assertFalse(doc["graph_complete"])
                component.extractor = None
                await rag.aconfigure({"extraction": {"failure_policy": "disabled"}})
                doc = await rag.aadd_document(title="disabled", content="No extraction model.")
                self.assertFalse(doc["graph_complete"])

    async def test_graph_policies_and_capability_schema(self):
        for policy in ("required", "best_effort", "disabled"):
            with tempfile.TemporaryDirectory() as root:
                call = AsyncMock(return_value={"choices": [{"message": {"content": "broken JSON"}}]})
                component = RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=embedding),
                    extractor=TripleExtractor(model="test", completion_fn=call))
                async with LargeLanguageModel(root, components=[component], engines={}) as backend:
                    project = await backend.projects.acreate("test", components=["rag"], config=ProjectConfig(
                        component_configurations={"rag": {"extraction": {"failure_policy": policy, "repair_attempts": 0}}}))
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
                project = await backend.projects.acreate("quota", components=["rag"], config=ProjectConfig(
                    policies={"usage": {"project_max_calls": 1}},
                    component_configurations={"rag": {"provider": {"delay_seconds": 0}}}))
                rag = await project.components.aget("rag")
                with self.assertRaisesRegex(Exception, "exhausted"):
                    await rag.aadd_document(title="test", content="secret text")
                self.assertEqual(call.await_count, 1)
                receipts = await rag.amodel_usage()
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0]["diagnostic_code"], "provider_connection")
                self.assertNotIn("PRIVATE", json.dumps(receipts))
                self.assertEqual(await rag.alist_documents(), [])

    async def test_job_failure_retry_reuses_batch_and_cancellation_keeps_generation(self):
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
                project = await backend.projects.acreate("test", components=["rag"], config=ProjectConfig(
                    component_configurations={"rag": {"embedding_batch_size": 1, "provider": {"max_attempts": 1},
                        "embedding_batching": {"cache_max_bytes": 0}}}))
                rag = await project.components.aget("rag")
                job = await rag.aenqueue_document(title="test", content="first\n\nsecond", identifier="doc")
                with self.assertRaises(ProviderError):
                    await rag.arun_job(job["id"])
                self.assertEqual(await rag.alist_documents(), [])
                state = await rag.ajob(job["id"])
                self.assertEqual(state["ingestion"]["embedding_batches_completed"], 1)
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
