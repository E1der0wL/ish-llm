"""RAG public document and operation snapshot projections."""
from copy import deepcopy
from .chunk_records import RAGDocument
from .graph_records import GraphRecord
from .ingestion_records import EmbeddingEvidence


class DocumentView:
    INTERNAL_FIELDS = (EmbeddingEvidence.Field.VECTORS, GraphRecord.Field.GRAPH,
                       EmbeddingEvidence.Field.PROFILE, RAGDocument.Field.PREPARATION_CONFIGURATION,
                       RAGDocument.Field.PREPARATION_FINGERPRINT, RAGDocument.Field.EXTRACTION_MANIFEST)

    @classmethod
    def project(cls, document: dict) -> dict:
        return deepcopy({key: value for key, value in document.items() if key not in cls.INTERNAL_FIELDS})


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
        return {**snapshot, cls.CONFIGURATION_VERSION: configuration_version,
                cls.SOURCE_VERSION: source_version, cls.SEARCH_OPTIONS: deepcopy(search_options)}

