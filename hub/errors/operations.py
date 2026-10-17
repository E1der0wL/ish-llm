"""Explicit application action preconditions, separate from execution failures."""

from llm.core.contracts import Diagnostic


class OperationBlocked(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.diagnostic = Diagnostic(code, message)
