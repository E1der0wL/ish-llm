"""단일 청크 임베딩 무결성 계약. 저장/UI와 무관하다."""

from llm.core import embeddings


class SingleEmbeddingWire:
    DATA = "data"
    VECTOR = "embedding"
    RESULT_COUNT = 1
    INVALID_COUNT = "embedding_result_count"
    EXPECTED_ONE = "Single-chunk embedding requires exactly one result"


def value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)




def extract_single_embedding(response, *, dimensions=None):
    """한 요청의 한 벡터만 검증한다. 공급자의 index는 순서 근거가 아니다."""
    rows = value(response, SingleEmbeddingWire.DATA)
    if not isinstance(rows, list) or len(rows) != SingleEmbeddingWire.RESULT_COUNT:
        raise embeddings.EmbeddingIntegrityError(SingleEmbeddingWire.INVALID_COUNT, SingleEmbeddingWire.EXPECTED_ONE)
    return embeddings.validate_vectors([value(rows[0], SingleEmbeddingWire.VECTOR)], dimensions=dimensions)[0]
