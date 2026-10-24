"""문서를 벡터로 변환하는 재사용 모델 호출. 호출 결과를 반환하며 도메인 저장이나 Run 생성은 수행하지 않는다.

Embedding inference reusable from Engines, Tools, RAG or ordinary Python."""

from typing import Any

from ._client import ModelClient
from llm.core.model_protocol import EmbeddingResult, ModelOperation, copy_model
from llm.core.embeddings import validate_vectors
from llm.providers.schema import EmbeddingParameters


class EmbeddingModel(ModelClient):
    operation = ModelOperation.EMBEDDING
    FIRST_VECTOR = 0
    INVALID_EMBEDDING = "Embedding adapter must return EmbeddingResult"

    @classmethod
    def require_result(cls, response):
        if not isinstance(response, EmbeddingResult):
            raise TypeError(cls.INVALID_EMBEDDING)
        return response

    @property
    def enforced_configuration(self):
        return copy_model(self.adapter.embedding_enforced)

    def identity(self, **kwargs):
        return EmbeddingParameters({**self.params, **kwargs}).optional_model

    def extract_vector(self, response, **kwargs):
        """모델 출력 계약은 클라이언트가 소유하고 코퍼스 차원 계약은 RAG가 소유한다."""
        EmbeddingModel.require_result(response)
        return validate_vectors([response.vector],
            dimensions=EmbeddingParameters({**self.params, **kwargs}).optional_dimensions)[self.FIRST_VECTOR]

    def validate_vectors(self, vectors, **kwargs):
        return validate_vectors(vectors, dimensions=EmbeddingParameters({**self.params, **kwargs}).optional_dimensions)

    def __init__(self, *, adapter=ModelClient.ABSENT, **params) -> None:
        super().__init__(params, adapter=adapter)

    async def embed(self, input: Any, **kwargs: Any) -> EmbeddingResult:
        """Return one validated vector and usage; the adapter owns SDK cache options."""
        result = await self.adapter.embed(copy_model(input),
                                          {**copy_model(self.params), **copy_model(kwargs), **self.enforced_configuration},
                                          self.provider_options, decide=self._retry_decider)
        return EmbeddingModel.require_result(result)
