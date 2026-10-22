"""단일 청크 임베딩 무결성 계약. 저장/UI와 무관하다."""

from llm.core import embeddings




def value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)




def extract_single_embedding(response, *, dimensions=None):
    """한 요청의 한 벡터만 검증한다. 공급자의 index는 순서 근거가 아니다."""
    rows = value(response, "data")
    if not isinstance(rows, list) or len(rows) != 1:
        raise embeddings.EmbeddingIntegrityError("embedding_result_count", "Single-chunk embedding requires exactly one result")
    return embeddings.validate_vectors([value(rows[0], "embedding")], dimensions=dimensions)[0]
