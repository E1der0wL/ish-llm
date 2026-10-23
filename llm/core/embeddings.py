"""Finite nonzero vector integrity, shared by adapters and stored RAG data."""

import math
from llm.errors import CodedError

class EmbeddingIntegrityError(CodedError, ValueError):
    DIMENSION_MISMATCH = "embedding_dimension_mismatch"
    INVALID_VECTOR = "embedding_invalid_vector"
    EXPECTED_DIMENSIONS = "Expected dimensions must be a positive integer"
    EMPTY_VECTORS = "Embeddings require nonempty vectors"
    UNEQUAL_DIMENSIONS = "Embeddings require equal dimensions"
    NONFINITE_OR_ZERO = "Embeddings must be finite nonzero vectors"
    MINIMUM_DIMENSIONS = 1
    UNKNOWN_DIMENSIONS = 0

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def validate_vectors(vectors, *, dimensions=None):
    if dimensions is not None and (type(dimensions) is not int or dimensions < EmbeddingIntegrityError.MINIMUM_DIMENSIONS):
        raise EmbeddingIntegrityError(EmbeddingIntegrityError.DIMENSION_MISMATCH, EmbeddingIntegrityError.EXPECTED_DIMENSIONS)
    if not isinstance(vectors, list) or not vectors:
        raise EmbeddingIntegrityError(EmbeddingIntegrityError.DIMENSION_MISMATCH, EmbeddingIntegrityError.EMPTY_VECTORS)
    expected = dimensions or (len(vectors[0]) if isinstance(vectors[0], (list, tuple)) else EmbeddingIntegrityError.UNKNOWN_DIMENSIONS)
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or not expected or len(vector) != expected:
            raise EmbeddingIntegrityError(EmbeddingIntegrityError.DIMENSION_MISMATCH, EmbeddingIntegrityError.UNEQUAL_DIMENSIONS)
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in vector) or not any(vector):
            raise EmbeddingIntegrityError(EmbeddingIntegrityError.INVALID_VECTOR, EmbeddingIntegrityError.NONFINITE_OR_ZERO)
    return [list(vector) for vector in vectors]
