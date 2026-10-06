"""문서를 벡터로 변환하는 재사용 모델 호출. 호출 결과를 반환하며 도메인 저장이나 Run 생성은 수행하지 않는다.

Embedding inference reusable from Engines, Tools, RAG or ordinary Python."""

from collections.abc import Awaitable, Callable
from typing import Any, Optional

from ._client import ModelClient
from llm.providers.embeddings import extract_single_embedding, validate_vectors


class EmbeddingModel(ModelClient):
    operation = "aembedding"
    enforced_configuration = {"caching": False, "cache": {"no-cache": True, "no-store": True}}

    def identity(self, **kwargs):
        return {**self.params, **kwargs}.get("model")

    def extract_vector(self, response, **kwargs):
        """모델 출력 계약은 클라이언트가 소유하고 코퍼스 차원 계약은 RAG가 소유한다."""
        return extract_single_embedding(response, dimensions={**self.params, **kwargs}.get("dimensions"))

    def validate_vectors(self, vectors, **kwargs):
        return validate_vectors(vectors, dimensions={**self.params, **kwargs}.get("dimensions"))

    def __init__(self, *, embedding_fn: Optional[Callable[..., Awaitable[Any]]] = None,
                 **params: Any) -> None:
        super().__init__("aembedding", embedding_fn, params)

    async def embed(self, input: Any, **kwargs: Any) -> Any:
        """Return LiteLLM's native response, including vectors and reported usage.

        Per-call kwargs override defaults. Provider-specific input formats and
        model parameters are forwarded without an application allowlist.
        """
        # RAG 벡터 무결성 계약. 일반 ModelClient의 provider cache 인자는 변경하지 않는다.
        kwargs.update(caching=False, cache={"no-cache": True, "no-store": True})
        return await self._invoke(input=input, **kwargs)
