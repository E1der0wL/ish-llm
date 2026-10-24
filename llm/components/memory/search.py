"""Memory search semantics; the Component owns record access and host callbacks."""

import math
import re

from .configuration import MemoryConfiguration
from .records import MemoryRecord, MemorySearchHit


class MemorySearch:
    ABSENT = None
    WORD = r"\w+"
    SEPARATOR = " "
    EMPTY_SCORE = 0.0
    KEYWORD_CUTOFF = 0
    INVALID_QUERY = "Memory query must be nonempty text"
    MISSING_STRATEGY = "Memory search requires config.search_strategy or a host search_fn"
    INVALID_LIMIT = "Memory search limit is outside configured bounds"
    INVALID_SCORES = "Memory search adapter must return finite scores for visible memory IDs"

    @classmethod
    def require_query(cls, query):
        if not isinstance(query, str) or not query.strip():
            raise ValueError(cls.INVALID_QUERY)

    @classmethod
    def require_strategy(cls, config, search_fn, *, keyword):
        if search_fn is cls.ABSENT and config.search_strategy != keyword:
            raise ValueError(cls.MISSING_STRATEGY)

    @classmethod
    def limit(cls, config, requested):
        limit = config.search_limit if requested is cls.ABSENT else requested
        if limit is not cls.ABSENT and (type(limit) is not int or limit < MemoryConfiguration.MINIMUM_RESULT_LIMIT
                or config.maximum_results is not cls.ABSENT and limit > config.required_maximum_results):
            raise ValueError(cls.INVALID_LIMIT)
        if limit is cls.ABSENT:
            limit = config.maximum_results
        return limit

    @classmethod
    def score(cls, query, record):
        terms = set(re.findall(cls.WORD, query.casefold()))
        text = cls.SEPARATOR.join([MemoryRecord(record).content, MemoryRecord(record).kind,
                                  *MemoryRecord(record).tags]).casefold()
        return sum(term in text for term in terms) / len(terms) if terms else cls.EMPTY_SCORE

    @classmethod
    def select(cls, scores, records, config, *, search_fn, limit):
        if not isinstance(scores, dict) or scores.keys() - records.keys() or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in scores.values()):
            raise ValueError(cls.INVALID_SCORES)
        # Host adapters own their result IDs, including zero/negative scores.
        # Only the keyword algorithm requires a positive match; cutoff is explicit.
        hits = [MemorySearchHit.create(score, records[key]) for key, score in scores.items()
                if (search_fn is not cls.ABSENT or score > cls.KEYWORD_CUTOFF)
                and (not config.has_min_score or score >= config.min_score)]
        return sorted(hits, key=lambda hit: MemorySearchHit(hit).rank)[:limit]
