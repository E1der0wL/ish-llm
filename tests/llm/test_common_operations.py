"""Nonstream operations accept common results without an OpenAI response envelope."""
import json
import unittest

from llm.core.model_protocol import ModelMessage, ModelRequest, ModelResponse, StopReason
from llm.providers.operations import EmbeddingResult, RankedDocument, RerankResult
from llm.components.rag.embedding import EmbeddingModel
from llm.components.rag.rerank import RerankModel
from llm.components.rag.extraction import TripleExtractor
from llm.components.vision.backends import VisionModel


class CommonOperations:
    vision_enforced = {}

    def __init__(self):
        self.requests = []

    def describe_config(self, operation='acompletion'):
        return {'type': 'object'}

    def validate_options(self, options, **context):
        # Deliberately legal here, unlike an SDK HTTP endpoint configuration.
        assert options.get('api_base') == 'local:fake'

    def validate_vision(self, options):
        self.validate_options(options)

    async def complete(self, request, options, **context):
        assert isinstance(request, ModelRequest)
        self.requests.append(request)
        content = json.dumps({'entities': [], 'relations': []})
        return ModelResponse(ModelMessage('assistant', content, opaque={'fake': {'seal': 'signed'}}),
                             StopReason.COMPLETE)

    async def embed(self, inputs, parameters, options, **context):
        self.requests.append(inputs)
        return EmbeddingResult((0.25, 0.75), None)

    async def rerank(self, query, documents, parameters, options, **context):
        self.requests.append(documents)
        return RerankResult((RankedDocument(1, 0.9), RankedDocument(0, 0.1)), None)


class CommonOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_client_results_are_rejected_before_consumption(self):
        from unittest.mock import AsyncMock
        adapter = CommonOperations()
        adapter.embed = AsyncMock(return_value={'data': [{'embedding': [0.25, 0.75]}]})
        adapter.rerank = AsyncMock(return_value={'results': [{'index': 0, 'relevance_score': 1}]})
        parameters = {'model': 'fake', 'api_base': 'local:fake'}
        with self.assertRaisesRegex(TypeError, 'EmbeddingResult'):
            await EmbeddingModel(adapter=adapter, **parameters).embed('text')
        with self.assertRaisesRegex(TypeError, 'RerankResult'):
            await RerankModel(adapter=adapter, **parameters).rerank('q', ['a'])

        adapter.complete = AsyncMock(return_value={'choices': [{'message': {'content': '{}'}}]})
        with self.assertRaisesRegex(TypeError, 'ModelResponse'):
            await TripleExtractor(adapter=adapter, **parameters).extract([{'id': 's', 'text': 'text'}])
        with self.assertRaisesRegex(TypeError, 'ModelResponse'):
            await VisionModel(adapter=adapter).analyze(b'image', 'image/png', 'read',
                                                      parameters=parameters, provider={})
        self.assertEqual(adapter.complete.await_count, 2)

    async def test_typed_ranking_cannot_reference_missing_document(self):
        from unittest.mock import AsyncMock
        adapter = CommonOperations()
        adapter.rerank = AsyncMock(return_value=RerankResult((RankedDocument(1, 0.9),)))
        with self.assertRaisesRegex(ValueError, 'outside'):
            await RerankModel(adapter=adapter, model='fake', api_base='local:fake').rerank('q', ['a'])

    def test_removed_callable_and_raw_apis_are_not_silently_accepted(self):
        from llm.providers.operations import LiteLLMOperations
        for client, keyword in ((EmbeddingModel, 'embedding_fn'), (RerankModel, 'rerank_fn'),
                                (TripleExtractor, 'completion_fn')):
            with self.subTest(client=client.__name__), self.assertRaisesRegex(TypeError, 'adapter'):
                client(model='fake', **{keyword: lambda **kw: None})
        for target, names in ((LiteLLMOperations(), ('invoke', 'embed_raw')),
                              (EmbeddingModel(), ('embed_result', 'extract_vector')),
                              (RerankModel(), ('rank',))):
            for name in names:
                self.assertFalse(hasattr(target, name), (type(target).__name__, name))

    async def test_embedding_extraction_rerank_and_multimodal_vision(self):
        adapter = CommonOperations()
        parameters = {'model': 'fake', 'api_base': 'local:fake'}
        embedding = EmbeddingModel(adapter=adapter, **parameters).with_config(parameters)
        result = await embedding.embed('text')
        self.assertEqual(result.vector, (0.25, 0.75))
        self.assertIsNone(result.usage)
        ranking = await RerankModel(adapter=adapter, **parameters).rerank('q', ['a', 'b'])
        self.assertEqual([r.index for r in ranking.documents], [1, 0])
        extracted = await TripleExtractor(adapter=adapter, **parameters).extract([{'id': 's', 'text': 'text'}])
        self.assertEqual(extracted, {'entities': [], 'relations': []})
        vision = await VisionModel(adapter=adapter).analyze(b'image', 'image/png', 'read',
                                                           parameters=parameters, provider={})
        self.assertEqual(vision['text'], json.dumps(extracted))
        parts = adapter.requests[-1].messages[0].content
        self.assertEqual([part.kind for part in parts], ['text', 'image'])
        self.assertEqual(parts[1].media_type, 'image/png')
        self.assertIsNone(vision['finish_reason'])
