"""Explicit RAG Job admission policy; no default concurrency is materialized."""
from llm.core.schema import SchemaType, object_schema, field


class JobAdmission:
    KEY = "ingestion"
    MAX_ACTIVE = "max_active"
    MINIMUM_ACTIVE = 1
    INITIAL_ACTIVE = 0
    ACTIVE_INCREMENT = 1
    DESCRIPTION = "동시 색인 작업 수"
    INTEGER_REQUIRED = "ingestion.max_active requires an integer"
    POSITIVE_REQUIRED = "ingestion.max_active must be positive"
    CAPACITY_REACHED = "rag_capacity: indexing concurrency limit reached; job remains queued"

    @classmethod
    def schema(cls):
        return object_schema({cls.MAX_ACTIVE: field(SchemaType.INTEGER, cls.DESCRIPTION, minimum=cls.MINIMUM_ACTIVE)})

