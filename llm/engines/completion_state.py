"""Transient reception evidence for one model attempt, never persisted retry authority."""

from dataclasses import dataclass
from typing import ClassVar


@dataclass(slots=True)
class CompletionProgress:
    NOT_RECEIVED: ClassVar[bool] = False
    RECEIVED: ClassVar[bool] = True

    received: bool = NOT_RECEIVED

    def accept_chunk(self):
        self.received = self.RECEIVED
