"""BM25·벡터 순위 결합과 출처/부모 문맥 확장. 저장이나 모델 호출은 하지 않는다."""

import re
from collections import OrderedDict
from threading import RLock
from heapq import nlargest

from .indexing import collection


def search_defaults():
    return {"method": "hybrid", "expand": "section", "limit": 5, "rerank": False,
            "max_hops": 2, "relation_limit": 30, "candidate_count": 20, "rrf_constant": 60}


def search_schema():
    from llm.core.schema import object_schema, field
    defaults = search_defaults()
    return object_schema({
        "method": field("string", defaults["method"], enum=["hybrid", "bm25", "vector"]),
        "expand": field("string", defaults["expand"], enum=["chunk", "section", "document"]),
        "limit": field("integer", defaults["limit"], minimum=1, maximum=100),
        "rerank": field("boolean", defaults["rerank"]),
        "max_hops": field("integer", defaults["max_hops"], minimum=1, maximum=5),
        "relation_limit": field("integer", defaults["relation_limit"], minimum=1, maximum=1000),
        "candidate_count": field("integer", defaults["candidate_count"], minimum=1),
        "rrf_constant": field("integer", defaults["rrf_constant"], minimum=1)})


def tokens(text: str) -> list:
    words = re.findall(r"[\w]+", text.lower())
    return words + [word[i:i + 2] for word in words for i in range(len(word) - 1)]


class LexicalCache:
    """컴포넌트가 소유하는 제한된 BM25 캐시. 불변 세대 경로를 키로 쓰고 원문 글자 수로 제한한다."""

    def __init__(self, max_chars):
        if type(max_chars) is not int or max_chars < 0:
            raise ValueError("search_cache_chars must be nonnegative")
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
            size = max(1, sum(len(c["text"]) for c in chunks.values()))
            model = BM25Okapi([tokens(c["text"]) or ["__empty__"] for c in chunks.values()])
            if size <= maximum:
                while self.entries and self.used + size > maximum:
                    _, (removed, _) = self.entries.popitem(last=False)
                    self.used -= removed
                self.entries[path] = (size, model)
                self.used += size
            return model


def search(path, documents: dict, query: str, vector, *, method: str, expand: str, limit: int,
           lexical_cache=None, cache_chars=None, candidate_count=20, rrf_constant=60) -> list:
    chunks = {c["id"]: c for doc in documents.values() for c in doc["chunks"]}
    if not chunks:
        return []
    order, rankings = list(chunks), []
    if method in ("hybrid", "vector"):
        with collection(path / "chroma") as index:
            rankings.append(index.query(query_embeddings=[vector], n_results=min(len(order), max(candidate_count, limit)),
                                        include=["distances"])["ids"][0])
    if method in ("hybrid", "bm25"):
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
        doc = documents[chunk["document_id"]]
        context = (doc["content"] if expand == "document" else
                   doc["sections"][chunk["section_id"]]["text"] if expand == "section" else chunk["text"])
        hits.append({**chunk, "title": doc["title"], "metadata": doc["metadata"],
                     "revision": doc["revision"], "context": context, "score": scores[identifier]})
    return hits
