"""문서 분할·임베딩·색인·검색을 소유하는 RAG 컴포넌트."""

from .errors import RAGConflictError
from .status import RAGJobStatus
from .embedding import EmbeddingModel
from .rerank import RerankModel
from .extraction import TripleExtractor

_COMPONENT_EXPORTS = ("RAGComponent",)
__all__ = [RAGJobStatus.__name__, *_COMPONENT_EXPORTS, RAGConflictError.__name__,
           EmbeddingModel.__name__, RerankModel.__name__, TripleExtractor.__name__]


def __getattr__(name):
    if name in _COMPONENT_EXPORTS:
        from . import component
        return getattr(component, name)
    raise AttributeError(name)
