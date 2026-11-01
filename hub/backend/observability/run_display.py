"""Detached fields needed by Hub's display, never a resumable/executable Run."""

from copy import deepcopy
from dataclasses import dataclass

from llm.core.agents import AgentBinding
from llm.core.continuations import ResumeRecord
from llm.core.results import CompletionRecords


@dataclass(frozen=True, slots=True)
class RunDisplay:
    id: str
    session_id: str
    engine: str
    status: str
    started_at: str | None
    ended_at: str | None
    error: str | None
    metadata: dict

    @classmethod
    def capture(cls, run, previous=None):
        # Preserve the complete declared receipts for their existing validators.
        # Policies, tool schemas, request context and execution bindings are not
        # display state and must not be copied for each streamed observation.
        old = previous.metadata if previous is not None else {}
        metadata = {key: old[key] if key in old and old[key] == run.metadata[key]
                    else deepcopy(run.metadata[key]) for key in (
            CompletionRecords.KEY, AgentBinding.METADATA_FIELD, ResumeRecord.KEY)
            if key in run.metadata}
        result = cls(run.id, run.session_id, run.engine, run.status, run.started_at,
                     run.ended_at, run.error, metadata)
        return previous if result == previous else result
