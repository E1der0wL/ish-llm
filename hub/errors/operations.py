"""Explicit application action preconditions, separate from execution failures."""

from llm.core.contracts import Diagnostic
from enum import StrEnum


class OperationCode(StrEnum):
    BUSY = "hub_operation_busy"
    CHANGED = "hub_operation_changed"
    NOT_RESUMABLE = "hub_not_resumable"
    EXISTING_RAG_JOB = "hub_existing_rag_job"
    EDIT_UNAVAILABLE = "hub_edit_unavailable"


class OperationBlocked(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.diagnostic = Diagnostic(code, message)
