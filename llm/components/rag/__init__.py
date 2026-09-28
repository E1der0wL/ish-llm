"""문서 분할·임베딩·색인·검색을 소유하는 RAG 컴포넌트."""

from .embedding import EmbeddingModel
from .rerank import RerankModel
from .extraction import TripleExtractor

__all__ = ["RAGComponent", "RAGConflictError", "EmbeddingModel", "RerankModel", "TripleExtractor"]


def __getattr__(name):
    if name in ("RAGComponent", "RAGConflictError"):
        from . import component
        return getattr(component, name)
    raise AttributeError(name)
