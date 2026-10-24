"""BM25·벡터 순위 결합과 출처/부모 문맥 확장. 저장이나 모델 호출은 하지 않는다."""

from .chunk_records import RAGChunk, RAGSection, SplitDocument

import re
from enum import StrEnum
from jsonschema import Draft202012Validator
from collections import OrderedDict
from threading import RLock
from heapq import nlargest

from .indexing import collection


class SearchMethod(StrEnum):
    HYBRID = "hybrid"
    BM25 = "bm25"
    VECTOR = "vector"


class SearchExpansion(StrEnum):
    CHUNK = "chunk"
    SECTION = "section"
    DOCUMENT = "document"


class RelationRanking(StrEnum):
    SOURCE = "source"
    SUPPORT_COUNT = "support_count"


def search_schema():
    from llm.core.schema import object_schema, field
    return object_schema({
        "method": field("string", enum=[mode.value for mode in SearchMethod]),
        "expand": field("string", enum=[mode.value for mode in SearchExpansion]),
        "limit": field("integer", minimum=1),
        "rerank": field("boolean"),
        "max_hops": field("integer", minimum=1),
        "relation_limit": field("integer", minimum=1),
        "relation_ranking": field("string", enum=[mode.value for mode in RelationRanking]),
        "candidate_count": field("integer", minimum=1),
        "rrf_constant": field("integer", minimum=1)})


def validate_search_options(values):
    """Validate explicit options against the same declaration used by configuration."""
    schema = search_schema()
    error = next(Draft202012Validator(schema).iter_errors(values), None)
    if error is not None:
        raise ValueError("Invalid RAG search options: " + error.message)
    for key, spec in schema["properties"].items():
        if key in values and spec["type"] == "integer" and type(values[key]) is not int:
            raise ValueError(f"{key} must be an integer")


def retrieval_schema():
    """Public document retrieval arguments, excluding index-only tuning."""
    from llm.core.schema import object_schema
    specs = search_schema()["properties"]
    return object_schema({"query": {"type": "string", "minLength": 1},
        **{name: specs[name] for name in ("method", "expand", "limit", "rerank", "max_hops", "relation_limit")}},
        additionalProperties=False, required=["query"])


def tokens(text: str) -> list:
    words = re.findall(r"[\w]+", text.lower())
    return words + [word[i:i + 2] for word in words for i in range(len(word) - 1)]


class LexicalCache:
    """컴포넌트가 소유하는 제한된 BM25 캐시. 불변 세대 경로를 키로 쓰고 원문 글자 수로 제한한다."""

    @staticmethod
    def capacity_schema():
        return {"type": "integer", "minimum": 0}

    def __init__(self, max_chars):
        from llm.core.schema import validate_declared_fields
        validate_declared_fields({"max_chars": max_chars}, {"max_chars": self.capacity_schema()})
        self.max_chars, self.used = max_chars, 0
        self.entries, self.lock = OrderedDict(), RLock()

    def get(self, path, chunks, *, max_chars=None):
        from rank_bm25 import BM25Okapi
        with self.lock:
            maximum = self.max_chars if max_chars is None else max_chars
            while self.entries and self.used > maximum:
                _, (removed, _) = self.entries.popitem(last=False)
                self.used -= removed
            existing = self.entries.pop(path, None)
            if existing is not None:
                self.entries[path] = existing
                return existing[1]
            size = max(1, sum(len(RAGChunk(c).text) for c in chunks.values()))
            model = BM25Okapi([tokens(RAGChunk(c).text) or ["__empty__"] for c in chunks.values()])
            if size <= maximum:
                while self.entries and self.used + size > maximum:
                    _, (removed, _) = self.entries.popitem(last=False)
                    self.used -= removed
                self.entries[path] = (size, model)
                self.used += size
            return model


def search(path, documents: dict, query: str, vector, *, method: str, expand: str, limit: int,
           lexical_cache=None, cache_chars=None, candidate_count, rrf_constant) -> list:
    chunks = {RAGChunk(c).identifier: c for doc in documents.values() for c in SplitDocument(doc).chunks}
    if not chunks:
        return []
    order, rankings = list(chunks), []
    if method in (SearchMethod.HYBRID, SearchMethod.VECTOR):
        with collection(path / "chroma") as index:
            rankings.append(index.query(query_embeddings=[vector], n_results=min(len(order), max(candidate_count, limit)),
                                        include=["distances"])["ids"][0])
    if method in (SearchMethod.HYBRID, SearchMethod.BM25):
        # 공백/기호뿐인 문단도 빈 토큰 배열로 BM25를 깨뜨리지 않는다.
        index = (lexical_cache or LexicalCache(0)).get(path, chunks, max_chars=cache_chars)
        scores = index.get_scores(tokens(query))
        terms = set(tokens(query))
        matched = [i for i, words in enumerate(index.doc_freqs) if terms.intersection(words)]
        rankings.append([order[i] for i in sorted(matched, key=lambda i: -scores[i])])
    scores = {}
    for ranking in rankings:
        for rank, identifier in enumerate(ranking):
            scores[identifier] = scores.get(identifier, 0) + 1 / (rrf_constant + 1 + rank)
    hits = []
    # 후보 전체의 RRF 점수는 유지하고 반환할 상위 항목만 정렬한다. 동점 순서도 유지한다.
    for identifier in nlargest(limit, scores, key=scores.get):
        chunk = chunks[identifier]
        doc = documents[RAGChunk(chunk).document_id]
        context = (SplitDocument(doc).content if expand == SearchExpansion.DOCUMENT else
                   RAGSection(SplitDocument(doc).sections[RAGChunk(chunk).section_id]).text if expand == SearchExpansion.SECTION else RAGChunk(chunk).text)
        hits.append({**chunk, "title": doc["title"], "metadata": doc["metadata"],
                     "generation": doc["generation"], "revision": doc["revision"], "context": context, "score": scores[identifier]})
    return hits
