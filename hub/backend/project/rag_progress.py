"""RAG observations; persisted Job state remains authoritative."""

from dataclasses import dataclass, field
from asyncio import Task
from datetime import datetime
import time

from llm.llm import Diagnostic
from ...errors.adapters import from_diagnostic
from ...errors.contracts import ErrorInfo, ErrorOrigin


def document_ids(job):
    return job["document_ids"] if "document_ids" in job else [job["document_id"]]


def job_entry(job, *, live=False, elapsed=None, plan=None):
    status = job["status"]
    if elapsed is None and status in ("failed", "cancelled", "completed"):
        if job.get("started_at") and job.get("ended_at"):
            elapsed = max(0, (datetime.fromisoformat(job["ended_at"]) -
                              datetime.fromisoformat(job["started_at"])).total_seconds())
    issue = job.get("diagnostic")
    failure = (from_diagnostic(Diagnostic.from_dict(issue), origin=ErrorOrigin.COMPONENT,
        operation="rag.register", source={"job_id": job["id"]}) if issue is not None and status == "failed" else None)
    return {"name": "job:" + job["id"], "kind": "job", "job_id": job["id"],
            "label": ", ".join(document_ids(job)), "status": status,
            "phase": "unconfirmed" if status == "running" and not live else job["phase"],
            "modified": (job.get("ended_at") if status in ("failed", "cancelled", "completed") else None)
                        or job.get("started_at") or job["created_at"],
            "description": job.get("error", "") if status == "failed" else "",
            "failure": failure, "ingestion": dict(job.get("ingestion", {})),
            "progress": dict(job.get("progress", {})), "elapsed": elapsed, "live": live,
            "failures": list(job.get("failures", [])),
            "retryable": (False if live or status in ("completed", "cancelled") or job.get("cancel_requested")
                or "resume_abandonment" in job else True if status == "queued"
                else plan.can_resume if plan is not None else None),
            "resume_blockers": tuple(item.to_dict() for item in plan.blockers) if plan is not None else ()}


@dataclass(slots=True)
class RegistrationProgress:
    job: dict
    task: Task | None = None
    started: float = field(default_factory=time.monotonic)
    phase_started: float = field(default_factory=time.monotonic)
    stats: dict = field(default_factory=dict)
    phase: str = "preparing"
    progress: dict = field(default_factory=dict)
    phase_seconds: dict = field(default_factory=dict)
    failure: ErrorInfo | None = None
    observation_error: ErrorInfo | None = None

    def set_phase(self, phase):
        if phase != self.phase:
            stamp = time.monotonic()
            self.phase_seconds[self.phase] = self.phase_seconds.get(self.phase, 0) + stamp - self.phase_started
            self.phase, self.phase_started = phase, stamp

    def update(self, progress):
        self.set_phase(progress.phase)
        self.progress = {"completed": progress.completed, "total": progress.total, "unit": progress.unit}

    def observe(self, event):
        if event.code not in ("rag_ingestion_progress", "rag_extraction_progress"):
            return
        self.stats.update({key: value for key, value in event.details.items() if type(value) in (int, float)})
        # Extraction totals and publish phases come only from the public API.
        if event.code == "rag_ingestion_progress":
            self.set_phase("embedding")
            self.progress = {"completed": self.stats.get("chunks_completed", 0),
                             "total": self.stats.get("chunks_total"), "unit": "chunk"}

    def entry(self, job=None):
        terminal = job is not None
        value = job_entry(job or {**self.job, "status": "running", "phase": self.phase},
                          live=not terminal, elapsed=time.monotonic() - self.started)
        times = dict(self.phase_seconds)
        times[self.phase] = times.get(self.phase, 0) + time.monotonic() - self.phase_started
        value.update(phase_seconds=times, observation_error=self.observation_error)
        if not terminal:
            value.update(ingestion=dict(self.stats), progress=dict(self.progress))
        if self.failure:
            value["failure"] = self.failure
        return value
