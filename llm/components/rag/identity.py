"""RAG public capability and Tool identities, independent of index/storage dependencies."""

from enum import StrEnum


class RAGIdentity:
    CAPABILITY = "rag"


class RAGToolName(StrEnum):
    SEARCH = "rag_search"
    LIST_DOCUMENTS = "rag_list_documents"
