"""RAG observations; persisted Job state remains authoritative."""

from dataclasses import dataclass, field
from asyncio import Task
from datetime import datetime
import time

from llm.llm import RAGJobStatus, Diagnostic
from ...errors.adapters import from_diagnostic
from ...errors.contracts import ErrorInfo, ErrorOrigin
from .rag_views import RAGEntry
from llm.components.rag.job_views import JobObservation, ProgressCounts
from llm.components.rag.job_records import JobProgress
from llm.components.rag.ingestion_records import IngestionObservation, IngestionCounts


def document_ids(job):
    return JobObservation(job).document_ids


def job_entry(
    job, *, live=RAGEntry.NOT_LIVE, elapsed=RAGEntry.ABSENT, plan=RAGEntry.ABSENT
):
    return RAGEntry.create(job, live=live, elapsed=elapsed, plan=plan)


@dataclass(slots=True)
class RegistrationProgress:
    job: dict
    task: Task | None = RAGEntry.ABSENT
    started: float = field(default_factory=time.monotonic)
    phase_started: float = field(default_factory=time.monotonic)
    stats: dict = field(default_factory=dict)
    phase: str = JobProgress.PREPARING
    progress: dict = field(default_factory=dict)
    phase_seconds: dict = field(default_factory=dict)
    failure: ErrorInfo | None = RAGEntry.ABSENT
    observation_error: ErrorInfo | None = RAGEntry.ABSENT

    def set_phase(self, phase):
        if phase != self.phase:
            stamp = time.monotonic()
            self.phase_seconds[self.phase] = (
                self.phase_seconds.get(self.phase, RAGEntry.ZERO)
                + stamp
                - self.phase_started
            )
            self.phase, self.phase_started = phase, stamp

    def update(self, progress):
        self.set_phase(progress.phase)
        self.progress = ProgressCounts.observe(progress)

    def observe(self, event):
        if event.code not in (
            IngestionObservation.EMBEDDING_EVENT,
            IngestionObservation.EXTRACTION_EVENT,
        ):
            return
        self.stats.update(
            {
                key: value
                for key, value in event.details.items()
                if type(value) in (int, float)
            }
        )
        # Extraction totals and publish phases come only from the public API.
        if event.code == IngestionObservation.EMBEDDING_EVENT:
            self.set_phase(IngestionObservation.EMBEDDING_PHASE)
            self.progress = ProgressCounts.create(
                IngestionCounts(self.stats).completed,
                IngestionCounts(self.stats).optional_total,
                IngestionObservation.CHUNK_UNIT,
            )

    def entry(self, job=RAGEntry.ABSENT):
        terminal = job is not RAGEntry.ABSENT
        value = job_entry(
            job
            or JobObservation.live(self.job, RAGJobStatus.RUNNING.value, self.phase),
            live=not terminal,
            elapsed=time.monotonic() - self.started,
        )
        times = dict(self.phase_seconds)
        times[self.phase] = (
            times.get(self.phase, RAGEntry.ZERO) + time.monotonic() - self.phase_started
        )
        RAGEntry.observation(value, times, self.observation_error)
        if not terminal:
            RAGEntry.live_counts(value, self.stats, self.progress)
        if self.failure:
            RAGEntry.failed(value, self.failure)
        return value
