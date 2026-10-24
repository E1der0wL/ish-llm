"""Embedding evidence and observations; workers never invent missing stored fields."""

import re

from llm.core.contracts import OperationProgress
from llm.core.embeddings import validate_vectors
from llm.services.infrastructure.storage import revision_token
from .chunk_records import RAGChunk, RAGContent


class BatchCheckpointName:
    EMBEDDING = "embedding"
    GRAPH = "graph"
    PATTERN = r"(?:d(0|[1-9][0-9]*)_)?(embedding|graph)_(0|[1-9][0-9]*)"
    FORMAT = "{kind}_{position}"
    DOCUMENT_FORMAT = "document_{position}"
    SCOPED_FORMAT = "d{position}_{key}"

    @classmethod
    def document(cls, position):
        return cls.DOCUMENT_FORMAT.format(position=position)

    @classmethod
    def scoped(cls, position, key):
        return cls.SCOPED_FORMAT.format(position=position, key=key)


    @classmethod
    def parse(cls, name):
        return re.fullmatch(cls.PATTERN, name)

    @classmethod
    def embedding(cls, position):
        return cls.FORMAT.format(kind=cls.EMBEDDING, position=position)

    @classmethod
    def graph(cls, position):
        return cls.FORMAT.format(kind=cls.GRAPH, position=position)


class EmbeddingCheckpoint:
    MINIMUM_CHUNK_SIZE = 1
    IDENTITY_PATTERN = r"[a-f0-9]{64}"
    ABSENT = None
    INCOMPLETE = "Incomplete embedding chunk checkpoint"
    SOURCE_CHANGED = "Embedding checkpoint original chunk changed"
    INVALID_IDENTITY = "Invalid embedding chunk checkpoint identity"
    INVALID_SIGNATURE = "Embedding chunk checkpoint signature mismatch"
    IDENTITY_MISMATCH = "Chunk checkpoint identity mismatch"

    class Field:
        FINGERPRINT = "fingerprint"
        TEXT_HASH = "text_hash"
        VECTOR = "vector"
        CHUNK = "chunk"
        CHUNK_SIZE = "chunk_size"

    FIELDS = frozenset((Field.FINGERPRINT, Field.TEXT_HASH, Field.VECTOR, Field.CHUNK, Field.CHUNK_SIZE))
    IDENTITY_FIELDS = (Field.FINGERPRINT, Field.TEXT_HASH)

    def __init__(self, value):
        self._value = value

    @classmethod
    def signature(cls, fingerprint, text_hash):
        return revision_token({cls.Field.FINGERPRINT: fingerprint, cls.Field.TEXT_HASH: text_hash})

    @classmethod
    def create(cls, fingerprint, text_hash, vector, chunk, chunk_size):
        return {cls.Field.FINGERPRINT: fingerprint, cls.Field.TEXT_HASH: text_hash,
                cls.Field.VECTOR: vector, cls.Field.CHUNK: chunk, cls.Field.CHUNK_SIZE: chunk_size}

    @classmethod
    def validate(cls, value, signature):
        if not isinstance(value, dict) or value.keys() != cls.FIELDS:
            raise ValueError(cls.INCOMPLETE)
        chunk = RAGChunk.validate(value[cls.Field.CHUNK])
        if (type(value[cls.Field.CHUNK_SIZE]) is not int or value[cls.Field.CHUNK_SIZE] < cls.MINIMUM_CHUNK_SIZE
                or RAGContent.digest(RAGChunk(chunk).text) != value[cls.Field.TEXT_HASH]):
            raise ValueError(cls.SOURCE_CHANGED)
        for key in cls.IDENTITY_FIELDS:
            if not isinstance(value[key], str) or re.fullmatch(cls.IDENTITY_PATTERN, value[key]) is cls.ABSENT:
                raise ValueError(cls.INVALID_IDENTITY)
        expected = cls.signature(value[cls.Field.FINGERPRINT], value[cls.Field.TEXT_HASH])
        if signature != expected:
            raise ValueError(cls.INVALID_SIGNATURE)
        validate_vectors([value[cls.Field.VECTOR]])
        return value

    @classmethod
    def require_identity(cls, value, fingerprint, text_hash):
        if (not isinstance(value, dict) or value.get(cls.Field.FINGERPRINT) != fingerprint
                or value.get(cls.Field.TEXT_HASH) != text_hash):
            raise ValueError(cls.IDENTITY_MISMATCH)

    @property
    def chunk(self):
        return self._value[self.Field.CHUNK]

    @property
    def vector(self):
        return self._value.get(self.Field.VECTOR)


class EmbeddingProfile:
    MINIMUM_DIMENSIONS = 1
    INVALID = "Invalid prepared RAG embedding profile"

    class Field:
        MODEL = "model"
        DIMENSIONS = "dimensions"

    FIELDS = frozenset((Field.MODEL, Field.DIMENSIONS))

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, model, dimensions):
        return {cls.Field.MODEL: model, cls.Field.DIMENSIONS: dimensions}

    @classmethod
    def validate(cls, profile):
        if (not isinstance(profile, dict) or profile.keys() != cls.FIELDS
                or not isinstance(profile[cls.Field.MODEL], str) or not profile[cls.Field.MODEL]
                or type(profile[cls.Field.DIMENSIONS]) is not int or profile[cls.Field.DIMENSIONS] < cls.MINIMUM_DIMENSIONS):
            raise ValueError(cls.INVALID)

    @property
    def model(self):
        return self._value[self.Field.MODEL]

    @property
    def dimensions(self):
        return self._value[self.Field.DIMENSIONS]


class EmbeddingEvidence:
    """Embedding evidence fields shared by prepared documents and corpus snapshots."""
    class Field:
        FINGERPRINT = "embedding_fingerprint"
        VECTORS = "vectors"
        PROFILE = "profile"
        INGESTION = "ingestion"

    def __init__(self, value):
        self._value = value

    @property
    def fingerprint(self):
        return self._value.get(self.Field.FINGERPRINT)

    @property
    def vectors(self):
        return self._value[self.Field.VECTORS]

    @property
    def profile(self):
        return EmbeddingProfile(self._value[self.Field.PROFILE])

    @property
    def optional_dimensions(self):
        return (self._value.get(self.Field.PROFILE) or {}).get(EmbeddingProfile.Field.DIMENSIONS)

    @property
    def observations(self):
        return self._value[self.Field.INGESTION]


class EmbeddingFingerprint:
    CONTRACT = "single-chunk-v1"

    class Field:
        CONTRACT = "contract"
        MODEL = "model"
        PARAMETERS = "params"
        DOCUMENT_OPTIONS = "document_kwargs"
        QUERY_OPTIONS = "query_kwargs"
        CLIENT = "client"

    @classmethod
    def create(cls, model, parameters, document_options, query_options, client):
        return revision_token({cls.Field.CONTRACT: cls.CONTRACT, cls.Field.MODEL: model,
            cls.Field.PARAMETERS: parameters, cls.Field.DOCUMENT_OPTIONS: document_options,
            cls.Field.QUERY_OPTIONS: query_options, cls.Field.CLIENT: client})


class IngestionObservation:
    ZERO = 0
    ZERO_SECONDS = 0.0
    METADATA_ONLY = True
    EMBEDDING_EVENT = "rag_ingestion_progress"
    EXTRACTION_EVENT = "rag_extraction_progress"
    EMBEDDING_PHASE = "embedding"
    EXTRACTION_PHASE = "extracting"
    CHUNK_UNIT = "chunk"
    BATCH_UNIT = "batch"

    class Field:
        TOTAL = "chunks_total"
        COMPLETED = "chunks_completed"
        REUSED = "chunks_reused"
        CACHED = "chunks_cached"
        REQUESTED = "chunks_requested"
        CONCURRENCY = "embedding_concurrency"
        SECONDS = "embedding_seconds"
        RETRIES = "provider_retries"
        CHECKPOINT_REUSE = "checkpoint_reuse"
        ACTIVE = "active_embedding_requests"
        PEAK = "peak_embedding_requests"
        EXTRACTION_SECONDS = "extraction_seconds"
        EXTRACTION_BATCHES = "extraction_batches"
        EXTRACTION_REUSED = "extraction_batches_reused"
        METADATA_ONLY = "metadata_only"

    @classmethod
    def embedding(cls, total, concurrency):
        return {cls.Field.TOTAL: total, cls.Field.COMPLETED: cls.ZERO, cls.Field.REUSED: cls.ZERO,
            cls.Field.CACHED: cls.ZERO, cls.Field.REQUESTED: cls.ZERO, cls.Field.CONCURRENCY: concurrency,
            cls.Field.SECONDS: cls.ZERO_SECONDS, cls.Field.RETRIES: cls.ZERO,
            cls.Field.CHECKPOINT_REUSE: cls.ZERO, cls.Field.ACTIVE: cls.ZERO, cls.Field.PEAK: cls.ZERO}

    @classmethod
    def metadata_only(cls, count):
        return {cls.Field.TOTAL: count, cls.Field.COMPLETED: count, cls.Field.REUSED: count,
            cls.Field.CACHED: cls.ZERO, cls.Field.REQUESTED: cls.ZERO, cls.Field.SECONDS: cls.ZERO_SECONDS,
            cls.Field.EXTRACTION_SECONDS: cls.ZERO_SECONDS, cls.Field.EXTRACTION_BATCHES: cls.ZERO,
            cls.Field.RETRIES: cls.ZERO, cls.Field.CHECKPOINT_REUSE: cls.ZERO, cls.Field.METADATA_ONLY: cls.METADATA_ONLY}

    @classmethod
    def start_extraction(cls, stats):
        stats.update({cls.Field.EXTRACTION_BATCHES: cls.ZERO, cls.Field.EXTRACTION_SECONDS: cls.ZERO_SECONDS,
                      cls.Field.EXTRACTION_REUSED: cls.ZERO})

    @classmethod
    def embedding_progress(cls, stats):
        return {**stats, OperationProgress.phase.__name__: cls.EMBEDDING_PHASE,
            OperationProgress.completed.__name__: stats[cls.Field.COMPLETED],
            OperationProgress.total.__name__: stats[cls.Field.TOTAL], OperationProgress.unit.__name__: cls.CHUNK_UNIT}

    @classmethod
    def extraction_progress(cls, stats, completed, total):
        return {**stats, OperationProgress.phase.__name__: cls.EXTRACTION_PHASE,
            OperationProgress.completed.__name__: completed, OperationProgress.total.__name__: total,
            OperationProgress.unit.__name__: cls.BATCH_UNIT}


class PreparationCallbacks:
    PROGRESS = "progress"
    TELEMETRY = "telemetry"

    @classmethod
    def prepare(cls, progress, telemetry):
        return {cls.PROGRESS: progress, cls.TELEMETRY: telemetry}

    @classmethod
    def publish(cls, progress):
        return {cls.PROGRESS: progress}
