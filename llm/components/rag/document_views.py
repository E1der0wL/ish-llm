"""RAG public document and operation snapshot projections."""

from copy import deepcopy
from .document_records import RAGDocument, RAGDocumentInput
from .graph_records import GraphRecord
from .ingestion_records import EmbeddingEvidence


class DocumentView:
    INTERNAL_FIELDS = (
        EmbeddingEvidence.Field.VECTORS,
        GraphRecord.Field.GRAPH,
        EmbeddingEvidence.Field.PROFILE,
        RAGDocument.Field.PREPARATION_CONFIGURATION,
        RAGDocument.Field.PREPARATION_FINGERPRINT,
        RAGDocument.Field.EXTRACTION_MANIFEST,
    )

    CREATED_FIELDS = (
        RAGDocument.Field.ID,
        RAGDocument.Field.TITLE,
        RAGDocument.Field.REVISION,
    )

    @classmethod
    def created(cls, document):
        return {key: document[key] for key in cls.CREATED_FIELDS}

    @classmethod
    def project(cls, document: dict) -> dict:
        return deepcopy(
            {
                key: value
                for key, value in document.items()
                if key not in cls.INTERNAL_FIELDS
            }
        )


class DocumentChange:
    EXPECTED_REVISION = "expected_revision"
    EXPECTED_GENERATION = "expected_generation"

    @classmethod
    def create(cls, revision, generation):
        return {cls.EXPECTED_REVISION: revision, cls.EXPECTED_GENERATION: generation}


class RAGDataSnapshot:
    CONFIGURATION_VERSION = "configuration_version"
    SOURCE_VERSION = "source_version"
    SEARCH_OPTIONS = "search_options"

    @classmethod
    def create(cls, snapshot, configuration_version, source_version, search_options):
        return {
            **snapshot,
            cls.CONFIGURATION_VERSION: configuration_version,
            cls.SOURCE_VERSION: source_version,
            cls.SEARCH_OPTIONS: deepcopy(search_options),
        }


class DocumentEdit:
    """Current document edit projection; the public data handle owns CAS and writes."""

    CONTENT_FIELDS = (
        RAGDocumentInput.Field.TITLE,
        RAGDocumentInput.Field.CONTENT,
        RAGDocumentInput.Field.METADATA,
    )
    EMPTY_DESCRIPTION = ""
    INVALID_FIELDS = (
        "RAG documents require title, content, metadata and optional description"
    )

    def __init__(self, value):
        self._value = value

    @property
    def revision(self):
        return self._value[RAGDocument.Field.REVISION]

    @property
    def generation(self):
        return self._value[RAGDocument.Field.GENERATION]

    @classmethod
    def version(cls, item):
        return {
            RAGDocument.Field.GENERATION: item[RAGDocument.Field.GENERATION],
            RAGDocument.Field.REVISION: item[RAGDocument.Field.REVISION],
        }

    @classmethod
    def content(cls, item):
        values = {key: item[key] for key in cls.CONTENT_FIELDS}
        values[RAGDocumentInput.Field.DESCRIPTION] = item.get(
            RAGDocumentInput.Field.DESCRIPTION, cls.EMPTY_DESCRIPTION
        )
        return values

    @classmethod
    def require_fields(cls, values):
        if not set(cls.CONTENT_FIELDS) <= values.keys() or values.keys() - {
            *cls.CONTENT_FIELDS,
            RAGDocumentInput.Field.DESCRIPTION,
        }:
            raise ValueError(cls.INVALID_FIELDS)
