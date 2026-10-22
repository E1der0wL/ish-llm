"""검색 후보를 재정렬하는 재사용 모델 호출. Tool, RAG, Engine에서 사용하되 실행 기록은 호출자가 소유한다.

Document reranking independent of execution strategy and storage."""

from collections.abc import Awaitable, Callable
from typing import Any, Optional, Union

from ._client import ModelClient
from llm.core.model_protocol import ModelOperation, RerankResult, copy_model


class RerankModel(ModelClient):
    operation = ModelOperation.RERANK

    def __init__(self, *, adapter=None, **params) -> None:
        super().__init__(params, adapter=adapter)

    async def rerank(self, query: str, documents: list[Union[str, dict[str, Any]]], **kwargs: Any) -> RerankResult:
        result = await self.adapter.rerank(query, copy_model(documents),
            {**copy_model(self.params), **copy_model(kwargs)}, self.provider_options, decide=self._retry_decider)
        if not isinstance(result, RerankResult):
            raise TypeError("Rerank adapter must return RerankResult")
        if any(row.index >= len(documents) for row in result.rows):
            raise ValueError("Ranking index is outside the supplied documents")
        return result
