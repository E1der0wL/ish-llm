"""Current corpus fields, strict document validation and snapshot projections."""

from .document_records import RAGDocument
from .ingestion_records import EmbeddingEvidence


class RAGCorpus:
    IDENTITY_WIDTH = 2
    GRAPH_SCHEMA_VERSION = 1
    ABSENT = None
    INVALID_VERSION = "Unsupported RAG graph schema; re-register source documents in a new RAG Project"
    INVALID_FIELDS = "Invalid RAG corpus fields"
    CHANGED_DOCUMENT = "RAG corpus document identity changed"
    CHANGED_PROFILE = "RAG corpus and document embedding profiles disagree"
    NONEMPTY_PROFILE = "Empty RAG corpus requires an explicit null profile"

    class Field:
        IDENTITY = "identity"
        GENERATION = "generation"
        DOCUMENTS = "documents"
        GRAPH_SCHEMA_VERSION = "graph_schema_version"

    def __init__(self, value):
        self._value = value

    @property
    def documents(self):
        return self._value[self.Field.DOCUMENTS]

    @property
    def binding(self):
        return self._value[self.Field.IDENTITY], self._value[self.Field.GENERATION]

    @property
    def identity(self):
        return self._value[self.Field.IDENTITY]

    @property
    def generation(self):
        return self._value[self.Field.GENERATION]

    @classmethod
    def create(cls, documents, profile):
        return {cls.Field.GRAPH_SCHEMA_VERSION: cls.GRAPH_SCHEMA_VERSION,
                cls.Field.DOCUMENTS: documents, EmbeddingEvidence.Field.PROFILE: profile}

    @classmethod
    def empty_snapshot(cls, identity):
        return {cls.Field.IDENTITY: identity, cls.Field.GENERATION: cls.ABSENT,
                cls.Field.DOCUMENTS: {}, EmbeddingEvidence.Field.PROFILE: cls.ABSENT}

    @classmethod
    def snapshot(cls, value, identity, generation):
        return {**value, cls.Field.IDENTITY: identity, cls.Field.GENERATION: generation}

    @classmethod
    def validate(cls, value, validate_document):
        if (not isinstance(value, dict) or type(value.get(cls.Field.GRAPH_SCHEMA_VERSION)) is not int
                or value[cls.Field.GRAPH_SCHEMA_VERSION] != cls.GRAPH_SCHEMA_VERSION):
            raise ValueError(cls.INVALID_VERSION)
        if value.keys() != {cls.Field.GRAPH_SCHEMA_VERSION, cls.Field.DOCUMENTS, EmbeddingEvidence.Field.PROFILE} or not isinstance(value[cls.Field.DOCUMENTS], dict):
            raise ValueError(cls.INVALID_FIELDS)
        documents = value[cls.Field.DOCUMENTS]
        for identifier, document in documents.items():
            validate_document(document)
            if RAGDocument(document).identifier != identifier:
                raise ValueError(cls.CHANGED_DOCUMENT)
            if document[EmbeddingEvidence.Field.PROFILE] != value[EmbeddingEvidence.Field.PROFILE]:
                raise ValueError(cls.CHANGED_PROFILE)
        if not documents and value[EmbeddingEvidence.Field.PROFILE] is not cls.ABSENT:
            raise ValueError(cls.NONEMPTY_PROFILE)
        return value

