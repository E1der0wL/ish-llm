"""RAG retrieval, source and catalog projections with their exact public field sets."""

from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean
from .chunk_records import RAGChunk
from .document_records import RAGDocument
from .corpus_records import RAGCorpus


class RetrievalHit:
    class Field:
        CONTEXT = "context"
        SCORE = "score"
        RERANK_SCORE = "rerank_score"

    @classmethod
    def create(cls, chunk, document, context, score):
        return {**chunk, RAGDocument.Field.TITLE: RAGDocument(document).title,
                RAGDocument.Field.METADATA: RAGDocument(document).metadata,
                RAGDocument.Field.GENERATION: RAGDocument(document).generation,
                RAGDocument.Field.REVISION: RAGDocument(document).revision,
                cls.Field.CONTEXT: context, cls.Field.SCORE: score}

    @classmethod
    def reranked(cls, hit, score):
        return {**hit, cls.Field.RERANK_SCORE: score}


class RetrievalSource:
    FIELDS = (RAGChunk.Field.ID, RAGChunk.Field.DOCUMENT_ID, RAGChunk.Field.SECTION_ID,
              RAGChunk.Field.HEADING, RAGChunk.Field.TEXT, RAGDocument.Field.TITLE,
              RAGDocument.Field.GENERATION, RAGDocument.Field.REVISION, RAGDocument.Field.METADATA)

    @classmethod
    def from_hit(cls, hit):
        return {key: hit[key] for key in cls.FIELDS}

    @classmethod
    def from_document(cls, chunk, document):
        return {**chunk, RAGDocument.Field.TITLE: RAGDocument(document).title,
                RAGDocument.Field.GENERATION: RAGDocument(document).generation,
                RAGDocument.Field.REVISION: RAGDocument(document).revision,
                RAGDocument.Field.METADATA: RAGDocument(document).metadata}


class CatalogCursor:
    INVALID = "Invalid catalog cursor"
    CONFLICT = "RAG catalog changed; restart pagination"

    class Field:
        IDENTITY = RAGCorpus.Field.IDENTITY
        GENERATION = RAGCorpus.Field.GENERATION
        AFTER = "after"

    FIELDS = (Field.IDENTITY, Field.GENERATION, Field.AFTER)

    def __init__(self, value):
        self._value = value

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.FIELDS):
            raise ValueError(cls.INVALID)

    @classmethod
    def create(cls, identity, generation, after):
        return {cls.Field.IDENTITY: identity, cls.Field.GENERATION: generation, cls.Field.AFTER: after}

    @classmethod
    def schema(cls):
        return {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
                SchemaKeyword.REQUIRED: list(cls.FIELDS), SchemaKeyword.PROPERTIES: {
                    key: {SchemaKeyword.TYPE: SchemaType.STRING} for key in cls.FIELDS}}

    @property
    def binding(self):
        return self._value[self.Field.IDENTITY], self._value[self.Field.GENERATION]

    @property
    def after(self):
        return self._value[self.Field.AFTER]


class CatalogRequest:
    MINIMUM_LIMIT = 1
    INVALID_LIMIT = "Catalog limit must be a positive integer"
    FIRST_AFTER = ""
    LOOKAHEAD = 1
    LAST_ROW = -1
    ABSENT = None

    class Field:
        LIMIT = "limit"
        CURSOR = "cursor"

    @classmethod
    def schema(cls):
        return {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
                SchemaKeyword.REQUIRED: [cls.Field.LIMIT], SchemaKeyword.PROPERTIES: {
                    cls.Field.LIMIT: {SchemaKeyword.TYPE: SchemaType.INTEGER, SchemaKeyword.MINIMUM: cls.MINIMUM_LIMIT},
                    cls.Field.CURSOR: CatalogCursor.schema()}}


class CatalogPage:
    class Field:
        DOCUMENTS = "documents"
        GENERATION = CatalogCursor.Field.GENERATION
        NEXT_CURSOR = "next_cursor"

    @classmethod
    def create(cls, documents, generation, cursor):
        return {cls.Field.DOCUMENTS: documents, cls.Field.GENERATION: generation, cls.Field.NEXT_CURSOR: cursor}


class RAGToolResult:
    COMPONENT = "component"

    @classmethod
    def project(cls, component, value):
        return {cls.COMPONENT: component, **value}
