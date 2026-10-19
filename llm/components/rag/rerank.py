"""검색 후보를 재정렬하는 재사용 모델 호출. Tool, RAG, Engine에서 사용하되 실행 기록은 호출자가 소유한다.

Document reranking independent of execution strategy and storage."""

from typing import Any, Union

from ._client import ModelClient
from llm.core.model_protocol import RerankResult
from llm.providers.parameters import copy_params


class RerankModel(ModelClient):
    operation = "arerank"

    def __init__(self, *, adapter=None, **params: Any) -> None:
        super().__init__("arerank", params, adapter=adapter)

    async def rerank(self, query: str, documents: list[Union[str, dict[str, Any]]],
                     **kwargs: Any) -> RerankResult:
        """Return normalized indexes, scores and usage."""
        result = await self.adapter.rerank(query, documents,
            {**copy_params(self.params), **copy_params(kwargs)}, self.provider_options, decide=self._retry_decider)
        if not isinstance(result, RerankResult):
            raise TypeError('Operation adapter must return RerankResult')
        if any(row.index >= len(documents) for row in result.documents):
            raise ValueError('Rerank index is outside the supplied documents')
        return result
