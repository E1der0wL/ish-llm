"""RAG manager jobs and observations through public component APIs only."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import time

from llm.providers.runtime import diagnostic_scope

from .record_management import DocumentManagement


def _document_ids(job):
    """Current single-document and atomic batch Job contracts."""
    return job["document_ids"] if "document_ids" in job else [job["document_id"]]


def job_entry(job, *, live=False, phase=None, ingestion=None, elapsed=None):
    stats = dict(job.get("ingestion", {}) if ingestion is None else ingestion)
    if elapsed is None and job["status"] in ("failed", "cancelled", "completed"):
        if job.get("started_at") and job.get("ended_at"):
            elapsed = max(0, (datetime.fromisoformat(job["ended_at"]) -
                              datetime.fromisoformat(job["started_at"])).total_seconds())
    status = job["status"]
    return {"name": "job:" + job["id"], "kind": "job", "job_id": job["id"],
            "label": ", ".join(_document_ids(job)), "status": status,
            "phase": phase or ("unconfirmed" if status == "running" else job["phase"]),
            "modified": (job.get("ended_at") if status in ("failed", "cancelled", "completed") else None)
                        or job.get("started_at") or job["created_at"],
            "description": job.get("error", "") if status == "failed" else "", "ingestion": stats, "elapsed": elapsed,
            "live": live, "retryable": not live and not job.get("cancel_requested") and "resume_abandonment" not in job and
            status in ("queued", "failed", "running")}


@dataclass(slots=True)
class RegistrationProgress:
    job: dict
    extraction: bool
    started: float = field(default_factory=time.monotonic)
    stats: dict = field(default_factory=dict)
    phase: str = "preparing"
    embedding_started: float | None = None

    def observe(self, event):
        if event.code not in ("rag_ingestion_progress", "rag_extraction_progress"):
            return
        if self.embedding_started is None:
            self.embedding_started = time.monotonic()
        # Diagnostics can also contain project/provider context. Keep only the
        # numeric counters; no document contents or model credentials enter UI.
        self.stats = {key: value for key, value in event.details.items()
                      if type(value) in (int, float)}
        total = self.stats.get("chunks_total", 0)
        if self.stats.get("chunks_completed", 0) < total:
            self.phase = "embedding"
        else:
            # Public jobs do not emit a separate publish-start event. Do not
            # invent a graph percentage or mistake reused graph batches for new calls.
            self.phase = "extraction_index" if self.extraction else "indexing"

    def entry(self):
        stats = dict(self.stats)
        if self.phase == "embedding" and self.embedding_started is not None:
            stats["embedding_seconds"] = time.monotonic() - self.embedding_started
        return job_entry({**self.job, "status": "running"}, live=True, phase=self.phase,
                         ingestion=stats, elapsed=time.monotonic() - self.started)


class RAGManagement(DocumentManagement):
    def __init__(self, data, name, root, project_id, active=None):
        super().__init__(data, name, root)
        self.project_id = project_id
        self.active = {} if active is None else active

    async def _run(self, identifier, *, retry, progress):
        job = await self.data.ajob(identifier)
        key = self.project_id, identifier
        if not job_entry(job)["retryable"] or key in self.active:
            raise ValueError("RAG job cannot be resumed in its current state")
        values = (await self.data.aresolve_config())["values"]
        observer = RegistrationProgress(job, values.get("policy", {}).get("extraction", {}).get("failure_policy") != "disabled")
        # Recheck after configuration I/O: another popup may have admitted it.
        if key in self.active:
            raise ValueError("RAG job is already running")
        self.active[key] = observer

        def report(entry):
            if progress:
                progress(entry)

        async def refresh():
            while True:
                report(observer.entry())
                await asyncio.sleep(.25)

        timer = asyncio.create_task(refresh())
        try:
            with diagnostic_scope(observer.observe):
                await self.data.arun_job(identifier, retry=retry)
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
            self.active.pop(key, None)
            report(job_entry(await self.data.ajob(identifier)))

    async def jobs(self):
        entries = []
        for job in await self.data.ajobs():
            if job["status"] in ("completed", "failed"):
                continue
            observer = self.active.get((self.project_id, job["id"]))
            entries.append(observer.entry() if observer else job_entry(job))
        return entries

    async def list(self):
        return [*await super().list(), *await self.jobs()]

    async def execute(self, action, argument="", expected_version=None, *, progress=None):
        if action == "jobs":
            return await self.jobs()
        if action == "import":
            identifier, values = await asyncio.to_thread(self._read_import, argument)
            # Never overwrite a completed document or duplicate an unfinished job.
            # Hidden failures must not block importing the current file again.
            try:
                await self.data.aget_document(identifier)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(identifier)
            if any(identifier in _document_ids(job) and job["status"] in ("queued", "running")
                   and "resume_abandonment" not in job
                   for job in await self.data.ajobs()):
                raise ValueError("An unfinished RAG job already exists; select it and retry")
            job = await self.data.aenqueue_document(identifier=identifier, **values)
            if progress:
                progress(job_entry(job))
            await self._run(job["id"], retry=False, progress=progress)
        elif action == "retry":
            await self._run(argument, retry=True, progress=progress)
        else:
            return await super().execute(action, argument, expected_version)
        return await self.list()
