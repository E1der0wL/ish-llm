"""RAG public capability and Tool identities, independent of index/storage dependencies."""

from enum import StrEnum
import re


class RAGIdentity:
    CAPABILITY = "rag"


class RAGToolName(StrEnum):
    SEARCH = "rag_search"
    LIST_DOCUMENTS = "rag_list_documents"


class RAGEvidenceIdentity:
    ABSENT = None
    INVALID = "RAG identity and generation require a stored UUID"
    PATTERN = re.compile(r"[a-f0-9]{32}")

    @classmethod
    def validate(cls, value):
        if not isinstance(value, str) or cls.PATTERN.fullmatch(value) is cls.ABSENT:
            raise ValueError(cls.INVALID)
