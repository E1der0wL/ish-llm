"""임베딩 무결성 계약과 근거가 있는 공급자 정규화. 저장/UI와 무관하다."""

import math
from copy import deepcopy
from .runtime import diagnostic


class EmbeddingIntegrityError(ValueError):
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


def validate_embeddings(response, count, *, dimensions=None):
    rows = value(response, "data")
    if not isinstance(rows, list) or len(rows) != count:
        raise EmbeddingIntegrityError("embedding_index_corruption", "Embedding result count does not match input")
    indexes = [value(row, "index") for row in rows]
    if any(type(i) is not int for i in indexes) or sorted(indexes) != list(range(count)):
        raise EmbeddingIntegrityError("embedding_index_corruption", "Embedding result indexes do not match input")
    ordered = sorted(rows, key=lambda row: value(row, "index"))
    return validate_vectors([value(row, "embedding") for row in ordered], dimensions=dimensions)


def normalize_litellm_embeddings(response, count, *, cache_read_allowed=False, cache_object=None,
                                 merge_positions=None):
    """cache_hit 하나만으로 순서를 추측하지 않는다. 호스트가 입증한 cache 위치가 필요하다.

    merge_positions는 SDK 병합 경계에서 확인한 cached 원래 위치의 집합이다.
    일반 llm 경로는 SDK cache를 끄므로 이 호환 분기에 진입하지 않는다.
    """
    rows = value(response, "data")
    if not isinstance(rows, list) or len(rows) != count:
        return response
    indexes = [value(row, "index") for row in rows]
    if all(type(i) is int for i in indexes) and sorted(indexes) == list(range(count)):
        return response
    hidden = value(response, "_hidden_params", {}) or {}
    if (not cache_read_allowed or cache_object is None or cache_object is False
            or hidden.get("cache_hit") is not True or merge_positions is None):
        return response
    cached = set(merge_positions)
    if not cached or len(cached) == count or any(type(i) is not int or not 0 <= i < count for i in cached):
        return response
    fresh = 0
    for position, index in enumerate(indexes):
        # cached item은 원래 위치 또는 단건 캐시 index=0, fresh는 miss subbatch 순서다.
        if type(index) is not int or (index not in (0, position) if position in cached else index != fresh):
            return response
        if position not in cached:
            fresh += 1
    validate_vectors([value(row, "embedding") for row in rows])
    result = deepcopy(response)
    for position, row in enumerate(value(result, "data")):
        if isinstance(row, dict):
            row["index"] = position
        else:
            row.index = position
    validate_embeddings(result, count)
    diagnostic("embedding_index_normalized", provider="litellm", actual=indexes,
               normalized=list(range(count)), reason="partial_cache_merge")
    return result
