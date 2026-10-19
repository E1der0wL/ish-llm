"""Hub defaults support ingestion and hybrid reranking with only model clients."""
from llm.providers.operations import LiteLLMOperations

import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from llm.components.rag import RAGComponent, EmbeddingModel, RerankModel
from tests.hub.test_components import embedding


class RagDefaultsTests(unittest.IsolatedAsyncioTestCase):
    async def test_models_only_ingest_search_and_preserve_explicit_settings(self):
        reranked = []
        async def rerank(**request):
            reranked.append(request)
            return {"results": [{"index": i, "relevance_score": 1.0}
                                for i in range(len(request["documents"]))]}
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, auto_title=False, component_factories=(lambda: RAGComponent(
                embedding=EmbeddingModel(model="test/embedding", adapter=LiteLLMOperations(call=embedding)),
                reranker=RerankModel(model="test/rerank", adapter=LiteLLMOperations(call=rerank))),),
                project_config={"parameters": {"components": {"rag": {"config": {"chunk_size": 900}}}}})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                rag = await runtime.project.components.aget("rag")
                await rag.aadd_document(title="Guide", content="Python coding guide", identifier="guide")
                result = await rag.asearch("Python coding")
                self.assertTrue(result)
                self.assertTrue(reranked)
                parameters = (await runtime.project.aget_data()).config.parameters["components"]["rag"]
                self.assertEqual(parameters["config"]["chunk_size"], 900)
                self.assertEqual(parameters["policy"]["extraction"]["failure_policy"], "disabled")
                self.assertNotIn("extraction_params", parameters["config"])
            finally:
                await runtime.close()
