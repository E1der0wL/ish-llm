"""문서를 벡터로 변환하는 재사용 모델 호출. 호출 결과를 반환하며 도메인 저장이나 Run 생성은 수행하지 않는다.

Embedding inference reusable from Engines, Tools, RAG or ordinary Python."""

from typing import Any

from ._client import ModelClient
from llm.providers.parameters import copy_params
from llm.providers.embeddings import validate_vectors
from llm.core.model_protocol import EmbeddingResult


class EmbeddingModel(ModelClient):
    operation = "aembedding"
    enforced_configuration = {"caching": False, "cache": {"no-cache": True, "no-store": True}}

    def identity(self, **kwargs):
        return {**self.params, **kwargs}.get("model")

    def validate_vectors(self, vectors, **kwargs):
        return validate_vectors(vectors, dimensions={**self.params, **kwargs}.get("dimensions"))

    def __init__(self, *, adapter=None, **params: Any) -> None:
        super().__init__("aembedding", params, adapter=adapter)

    async def embed(self, input: Any, **kwargs: Any) -> EmbeddingResult:
        """Return one validated vector and normalized usage from the selected adapter."""
        result = await self.adapter.embed(input, {**copy_params(self.params), **copy_params(kwargs)},
                                          self.provider_options, decide=self._retry_decider)
        if not isinstance(result, EmbeddingResult):
            raise TypeError('Operation adapter must return EmbeddingResult')
        self.validate_vectors([result.vector], **kwargs)
        return result
