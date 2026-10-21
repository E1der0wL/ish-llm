"""RAG Job lifecycle values shared by storage and public observations."""

from enum import StrEnum


class RAGJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
