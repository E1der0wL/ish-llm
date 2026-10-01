"""단일 청크 임베딩 무결성 계약. 저장/UI와 무관하다."""

import math
from llm.errors import CodedError


class EmbeddingIntegrityError(CodedError, ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def validate_vectors(vectors, *, dimensions=None):
    if dimensions is not None and (type(dimensions) is not int or dimensions < 1):
        raise EmbeddingIntegrityError("embedding_dimension_mismatch", "Expected dimensions must be a positive integer")
    if not isinstance(vectors, list) or not vectors:
        raise EmbeddingIntegrityError("embedding_dimension_mismatch", "Embeddings require nonempty vectors")
    expected = dimensions or (len(vectors[0]) if isinstance(vectors[0], (list, tuple)) else 0)
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or not expected or len(vector) != expected:
            raise EmbeddingIntegrityError("embedding_dimension_mismatch", "Embeddings require equal dimensions")
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in vector) or not any(vector):
            raise EmbeddingIntegrityError("embedding_invalid_vector", "Embeddings must be finite nonzero vectors")
    return [list(vector) for vector in vectors]


def extract_single_embedding(response, *, dimensions=None):
    """한 요청의 한 벡터만 검증한다. 공급자의 index는 순서 근거가 아니다."""
    rows = value(response, "data")
    if not isinstance(rows, list) or len(rows) != 1:
        raise EmbeddingIntegrityError("embedding_result_count", "Single-chunk embedding requires exactly one result")
    return validate_vectors([value(rows[0], "embedding")], dimensions=dimensions)[0]
