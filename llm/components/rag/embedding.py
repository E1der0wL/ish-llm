"""문서를 벡터로 변환하는 재사용 모델 호출. 호출 결과를 반환하며 도메인 저장이나 Run 생성은 수행하지 않는다.

Embedding inference reusable from Engines, Tools, RAG or ordinary Python."""

from collections.abc import Awaitable, Callable
from typing import Any, Optional

from ._client import ModelClient


class EmbeddingModel(ModelClient):
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
