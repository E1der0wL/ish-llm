"""문서 분할·임베딩·색인·검색을 소유하는 RAG 컴포넌트."""

from .status import RAGJobStatus
from .embedding import EmbeddingModel
from .rerank import RerankModel
from .extraction import TripleExtractor

_COMPONENT_EXPORTS = ("RAGComponent", "RAGConflictError")
__all__ = ["RAGJobStatus", *_COMPONENT_EXPORTS, "EmbeddingModel", "RerankModel", "TripleExtractor"]


def __getattr__(name):
    if name in _COMPONENT_EXPORTS:
        from . import component
        return getattr(component, name)
    raise AttributeError(name)
