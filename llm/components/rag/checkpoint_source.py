"""Bind RAG batch receipts to the unchanged, stored Job input."""

from llm.services.infrastructure.storage import revision_token
from .job_storage import JobStorage, JobBatchReceipt, JobDocumentBatch
from .job_records import JobRecord
from .ingestion_records import BatchCheckpointName, EmbeddingCheckpoint
from .graph_records import ExtractionCheckpoint, validate_extraction_checkpoint
from .document_records import RAGDocumentInput
from .chunk_records import SplitDocument
from .splitting import split_markdown


class RAGCheckpointSource:
    ABSENT = None
    CHANGED_POSITION = "RAG checkpoint document position changed"
    SINGLE_DOCUMENT = "Single-document Job cannot have a batch document checkpoint"
    CHANGED_CHUNKS = "RAG checkpoint differs from its original input chunks"

    @classmethod
    def validate(cls, job, key, receipt, payload):
        name = JobStorage.batch_name(key)
        batch = BatchCheckpointName.parse(name)
        if batch is cls.ABSENT:
            return
        if JobRecord.Field.DOCUMENT_IDS in job:
            if BatchCheckpointName.document_position(batch) is cls.ABSENT or int(
                BatchCheckpointName.document_position(batch)
            ) >= len(job[JobRecord.Field.DOCUMENT_IDS]):
                raise ValueError(cls.CHANGED_POSITION)
        elif BatchCheckpointName.document_position(batch) is not cls.ABSENT:
            raise ValueError(cls.SINGLE_DOCUMENT)
        source = (
            JobDocumentBatch.document(payload, int(BatchCheckpointName.document_position(batch)))
            if BatchCheckpointName.document_position(batch) is not cls.ABSENT
            else payload
        )
        value = JobBatchReceipt(receipt).value
        if BatchCheckpointName.kind(batch) == BatchCheckpointName.EMBEDDING:
            EmbeddingCheckpoint.validate(value, JobBatchReceipt(receipt).signature)
            actual = [EmbeddingCheckpoint(value).chunk]
            checkpoint = EmbeddingCheckpoint(value)
        else:
            validate_extraction_checkpoint(value, JobBatchReceipt(receipt).signature)
            actual = ExtractionCheckpoint(value).chunks
            checkpoint = ExtractionCheckpoint(value)
        chunks = SplitDocument(
            split_markdown(
                RAGDocumentInput(source).content,
                RAGDocumentInput(source).identifier,
                chunk_size=checkpoint.chunk_size,
            )
        ).chunks
        offset = int(BatchCheckpointName.offset(batch))
        if revision_token(chunks[offset : offset + len(actual)]) != revision_token(actual):
            raise ValueError(cls.CHANGED_CHUNKS)
