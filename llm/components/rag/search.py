"""BM25·벡터 순위 결합과 출처/부모 문맥 확장. 저장이나 모델 호출은 하지 않는다."""

from .chunk_records import RAGChunk, RAGSection, SplitDocument

import re
from enum import StrEnum
from jsonschema import Draft202012Validator
from collections import OrderedDict
from threading import RLock
from heapq import nlargest

from .indexing import collection, ChromaIndex
from .retrieval_records import RetrievalHit
from .identity import RAGIdentity
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean
from llm.core.implementation_parameters import ImplementationParameters


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


class SearchOptions:
    CONFIG_KEY = "search"
    SCOPE = RAGIdentity.CAPABILITY + "." + CONFIG_KEY
    MINIMUM = 1
    ABSENT = None
    INVALID = "Invalid RAG search options: "
    INTEGER_REQUIRED = "{key} must be an integer"
    RERANK_DISABLED = False

    class Field:
        METHOD = "method"
        EXPAND = "expand"
        LIMIT = "limit"
        RERANK = "rerank"
        MAX_HOPS = "max_hops"
        RELATION_LIMIT = "relation_limit"
        RELATION_RANKING = "relation_ranking"
        CANDIDATE_COUNT = "candidate_count"
        RRF_CONSTANT = "rrf_constant"

    @classmethod
    def configured(cls, values):
        return values.get(ImplementationParameters.CONFIG, {}).get(cls.CONFIG_KEY, {})


class RetrievalArguments:
    QUERY = "query"
    MINIMUM_QUERY_LENGTH = 1
    PUBLIC_OPTIONS = (SearchOptions.Field.METHOD, SearchOptions.Field.EXPAND, SearchOptions.Field.LIMIT,
                      SearchOptions.Field.RERANK, SearchOptions.Field.MAX_HOPS, SearchOptions.Field.RELATION_LIMIT)


class SearchTokens:
    WORD_PATTERN = r"[\w]+"
    BIGRAM_LENGTH = 2
    WINDOW_END_OFFSET = 1


class RankFusion:
    ABSENT = None
    ZERO_SCORE = 0
    NUMERATOR = 1
    FIRST_ORDINAL = 1


def search_schema():
    from llm.core.schema import object_schema, field
    return object_schema({
        SearchOptions.Field.METHOD: field(SchemaType.STRING, enum=[mode.value for mode in SearchMethod]),
        SearchOptions.Field.EXPAND: field(SchemaType.STRING, enum=[mode.value for mode in SearchExpansion]),
        SearchOptions.Field.LIMIT: field(SchemaType.INTEGER, minimum=SearchOptions.MINIMUM),
        SearchOptions.Field.RERANK: field(SchemaType.BOOLEAN),
        SearchOptions.Field.MAX_HOPS: field(SchemaType.INTEGER, minimum=SearchOptions.MINIMUM),
        SearchOptions.Field.RELATION_LIMIT: field(SchemaType.INTEGER, minimum=SearchOptions.MINIMUM),
        SearchOptions.Field.RELATION_RANKING: field(SchemaType.STRING, enum=[mode.value for mode in RelationRanking]),
        SearchOptions.Field.CANDIDATE_COUNT: field(SchemaType.INTEGER, minimum=SearchOptions.MINIMUM),
        SearchOptions.Field.RRF_CONSTANT: field(SchemaType.INTEGER, minimum=SearchOptions.MINIMUM)})


def validate_search_options(values):
    """Validate explicit options against the same declaration used by configuration."""
    schema = search_schema()
    error = next(Draft202012Validator(schema).iter_errors(values), SearchOptions.ABSENT)
    if error is not SearchOptions.ABSENT:
        raise ValueError(SearchOptions.INVALID + error.message)
    for key, spec in schema[SchemaKeyword.PROPERTIES].items():
        if key in values and spec[SchemaKeyword.TYPE] == SchemaType.INTEGER and type(values[key]) is not int:
            raise ValueError(SearchOptions.INTEGER_REQUIRED.format(key=key))


def retrieval_schema():
    """Public document retrieval arguments, excluding index-only tuning."""
    from llm.core.schema import object_schema
    specs = search_schema()[SchemaKeyword.PROPERTIES]
    return object_schema({RetrievalArguments.QUERY: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: RetrievalArguments.MINIMUM_QUERY_LENGTH},
        **{name: specs[name] for name in RetrievalArguments.PUBLIC_OPTIONS}},
        additionalProperties=SchemaBoolean.REJECT, required=[RetrievalArguments.QUERY])


def tokens(text: str) -> list:
    words = re.findall(SearchTokens.WORD_PATTERN, text.lower())
    return words + [word[i:i + SearchTokens.BIGRAM_LENGTH] for word in words for i in range(len(word) - SearchTokens.WINDOW_END_OFFSET)]


class LexicalCache:
    """컴포넌트가 소유하는 제한된 BM25 캐시. 불변 세대 경로를 키로 쓰고 원문 글자 수로 제한한다."""

    CAPACITY_KEY = "max_chars"
    DISABLED_CAPACITY = 0
    EMPTY_USAGE = 0
    ABSENT = None
    OLDEST_FIRST = False
    MODEL_INDEX = 1
    MINIMUM_ACCOUNTED_CHARS = 1
    EMPTY_TOKEN = "__empty__"

    @staticmethod
    def capacity_schema():
        return {SchemaKeyword.TYPE: SchemaType.INTEGER, SchemaKeyword.MINIMUM: LexicalCache.DISABLED_CAPACITY}

    def __init__(self, max_chars):
        from llm.core.schema import validate_declared_fields
        validate_declared_fields({self.CAPACITY_KEY: max_chars}, {self.CAPACITY_KEY: self.capacity_schema()})
        self.max_chars, self.used = max_chars, self.EMPTY_USAGE
        self.entries, self.lock = OrderedDict(), RLock()

    def get(self, path, chunks, *, max_chars=ABSENT):
        from rank_bm25 import BM25Okapi
        with self.lock:
            maximum = self.max_chars if max_chars is self.ABSENT else max_chars
            while self.entries and self.used > maximum:
                _, (removed, _) = self.entries.popitem(last=self.OLDEST_FIRST)
                self.used -= removed
            existing = self.entries.pop(path, self.ABSENT)
            if existing is not self.ABSENT:
                self.entries[path] = existing
                return existing[self.MODEL_INDEX]
            size = max(self.MINIMUM_ACCOUNTED_CHARS, sum(len(RAGChunk(c).text) for c in chunks.values()))
            model = BM25Okapi([tokens(RAGChunk(c).text) or [self.EMPTY_TOKEN] for c in chunks.values()])
            if size <= maximum:
                while self.entries and self.used + size > maximum:
                    _, (removed, _) = self.entries.popitem(last=self.OLDEST_FIRST)
                    self.used -= removed
                self.entries[path] = (size, model)
                self.used += size
            return model


def search(path, documents: dict, query: str, vector, *, method: str, expand: str, limit: int,
           lexical_cache=RankFusion.ABSENT, cache_chars=RankFusion.ABSENT, candidate_count, rrf_constant) -> list:
    chunks = {RAGChunk(c).identifier: c for doc in documents.values() for c in SplitDocument(doc).chunks}
    if not chunks:
        return []
    order, rankings = list(chunks), []
    if method in (SearchMethod.HYBRID, SearchMethod.VECTOR):
        with collection(path / ChromaIndex.DIRECTORY) as index:
            rankings.append(ChromaIndex.ranked_ids(index, vector, min(len(order), max(candidate_count, limit))))
    if method in (SearchMethod.HYBRID, SearchMethod.BM25):
        # 공백/기호뿐인 문단도 빈 토큰 배열로 BM25를 깨뜨리지 않는다.
        index = (lexical_cache or LexicalCache(LexicalCache.DISABLED_CAPACITY)).get(path, chunks, max_chars=cache_chars)
        scores = index.get_scores(tokens(query))
        terms = set(tokens(query))
        matched = [i for i, words in enumerate(index.doc_freqs) if terms.intersection(words)]
        rankings.append([order[i] for i in sorted(matched, key=lambda i: -scores[i])])
    scores = {}
    for ranking in rankings:
        for rank, identifier in enumerate(ranking):
            scores[identifier] = scores.get(identifier, RankFusion.ZERO_SCORE) + RankFusion.NUMERATOR / (rrf_constant + RankFusion.FIRST_ORDINAL + rank)
    hits = []
    # 후보 전체의 RRF 점수는 유지하고 반환할 상위 항목만 정렬한다. 동점 순서도 유지한다.
    for identifier in nlargest(limit, scores, key=scores.get):
        chunk = chunks[identifier]
        doc = documents[RAGChunk(chunk).document_id]
        context = (SplitDocument(doc).content if expand == SearchExpansion.DOCUMENT else
                   RAGSection(SplitDocument(doc).sections[RAGChunk(chunk).section_id]).text if expand == SearchExpansion.SECTION else RAGChunk(chunk).text)
        hits.append(RetrievalHit.create(chunk, doc, context, scores[identifier]))
    return hits
