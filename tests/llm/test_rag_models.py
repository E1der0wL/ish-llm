
from llm.providers.operations import LiteLLMOperations
import asyncio
import subprocess
import sys
import unittest
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, patch

from llm.components.rag import EmbeddingModel, RerankModel
from llm.core.model_protocol import EmbeddingResult, RankedDocument, RerankResult


class RAGModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedding_passes_open_kwargs_and_returns_common_vector_usage(self):
        response = SimpleNamespace(data=[{"embedding": [0.1, 0.2]}], usage={"total_tokens": 2})
        client = object()
        async def embed(**request):
            self.assertIs(request["client"], client)
            self.assertEqual(request["dimensions"], 2)
            self.assertEqual(request["provider_option"], {"modes": ["custom"]})
            self.assertEqual(request["timeout"], 12)
            self.assertNotIn("num_retries", request)
            self.assertNotIn("max_retries", request)
            request["input"].append("provider mutation")
            request["provider_option"]["modes"].clear()
            return response
        options = {"provider_option": {"modes": ["custom"]}}
        model = EmbeddingModel(model="openai/test", adapter=LiteLLMOperations(call=embed), client=client,
                               dimensions=100, **options)
        inputs = ["hello"]
        self.assertEqual(await model.embed(inputs, dimensions=2, timeout=12), EmbeddingResult((0.1, 0.2), {"total_tokens": 2}))
        self.assertEqual(inputs, ["hello"])
        self.assertEqual(options, {"provider_option": {"modes": ["custom"]}})
        self.assertEqual(model.params["provider_option"], options["provider_option"])

    async def test_rerank_accepts_document_objects_and_returns_scores_and_metadata(self):
        response = {"results": [{"index": 1, "relevance_score": 0.9}], "meta": {"billed_units": 1}}
        async def rerank(**request):
            self.assertEqual(request["model"], "cohere/override")
            self.assertEqual(request["query"], "query")
            self.assertEqual(request["top_n"], 1)
            self.assertEqual(request["return_documents"], True)
            request["documents"][0]["text"] = "changed"
            return response
        model = RerankModel(model="cohere/default", adapter=LiteLLMOperations(call=rerank), top_n=5)
        documents = [{"text": "first"}, {"text": "second"}]
        self.assertEqual(await model.rerank("query", documents, model="cohere/override",
                                        top_n=1, return_documents=True),
                         RerankResult((RankedDocument(1, 0.9),), opaque={"litellm": {"meta": {"billed_units": 1}}}))
        self.assertEqual(documents[0]["text"], "first")

    async def test_concurrent_calls_keep_inputs_separate(self):
        entered = []
        ready = asyncio.Event()
        async def embed(**request):
            entered.append(request)
            request["extra"]["values"].append(request["input"])
            if len(entered) == 2:
                ready.set()
            await ready.wait()
            return {"data": [{"embedding": [1 if request["input"] == "a" else 2]}],
                    "observed": request["extra"]["values"]}
        model = EmbeddingModel(model="test", adapter=LiteLLMOperations(call=embed), extra={"values": []})
        first, second = await asyncio.wait_for(asyncio.gather(model.embed("a"), model.embed("b")), 3)
        self.assertEqual(first.vector, (1,))
        self.assertEqual(first.opaque, {"litellm": {"observed": ["a"]}})
        self.assertEqual(second.vector, (2,))
        self.assertEqual(second.opaque, {"litellm": {"observed": ["b"]}})
        self.assertEqual(model.params["extra"], {"values": []})

    async def test_cancellation_propagates_to_async_provider(self):
        started, closed = asyncio.Event(), asyncio.Event()
        async def rerank(**request):
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
        model = RerankModel(model="test", adapter=LiteLLMOperations(call=rerank))
        task = asyncio.create_task(model.rerank("query", ["doc"]))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())

    async def test_missing_model_and_native_provider_error(self):
        call = AsyncMock()
        with self.assertRaises(ValueError):
            await EmbeddingModel(adapter=LiteLLMOperations(call=call)).embed("text")
        call.assert_not_called()
        failure = RuntimeError("provider diagnostic")
        call.side_effect = failure
        with self.assertRaises(RuntimeError) as raised:
            await EmbeddingModel(model="test", adapter=LiteLLMOperations(call=call)).embed("text")
        self.assertIs(raised.exception, failure)

    async def test_default_dispatch_uses_installed_litellm_async_entry_points(self):
        # Actual SDK import/API surface; provider entry points mocked, no network.
        import importlib
        import os
        import tiktoken
        # Do not rely on tokenizer files cached by another Python installation.
        # Token accounting is not under test in this API-dispatch case.
        encoding = tiktoken.Encoding(name="offline-inference-test", pat_str=r"(?s).",
                                     mergeable_ranks={bytes([i]): i for i in range(256)},
                                     special_tokens={})
        with patch.dict(os.environ, {"LITELLM_LOCAL_MODEL_COST_MAP": "True"}), \
                patch.object(tiktoken, "get_encoding", return_value=encoding), \
                patch.object(tiktoken, "encoding_for_model", return_value=encoding):
            sdk = await asyncio.to_thread(importlib.import_module, "litellm")
        self.assertTrue(callable(sdk.aembedding))
        self.assertTrue(callable(sdk.arerank))
        response = {"data": [{"embedding": [1, 2]}]}
        with patch.object(sdk, "aembedding", AsyncMock(return_value=response)) as embed:
            self.assertEqual(await EmbeddingModel(model="test").embed("text"), EmbeddingResult((1, 2)))
            embed.assert_awaited_once_with(model="test", input="text",
                caching=False, cache={"no-cache": True, "no-store": True}, num_retries=0, max_retries=0)
        with patch.object(sdk, "arerank", AsyncMock(return_value={"results": [{"index": 0, "relevance_score": 1}]})) as rerank:
            self.assertEqual(await RerankModel(model="test").rerank("q", ["d"]), RerankResult((RankedDocument(0, 1),)))
            rerank.assert_awaited_once_with(model="test", query="q", documents=["d"], num_retries=0, max_retries=0)

    def test_import_does_not_load_engines_services_or_sdk(self):
        # Pure core model values are now an explicit dependency; no lifecycle or SDK import.
        code = ("import sys; from llm.components.rag import EmbeddingModel, RerankModel; "
                "assert not any(n == 'litellm' or n.startswith(('llm.engines', 'llm.services')) "
                "for n in sys.modules)")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        for method in (EmbeddingModel.__init__, EmbeddingModel.embed, RerankModel.__init__, RerankModel.rerank):
            self.assertTrue(get_type_hints(method))
