"""RAG document editing plus resumable registration jobs in one shared popup."""

import asyncio

from prompt_toolkit.application.current import get_app

from ..controls import Button
from ..sidebar import SidebarItem
from .record_manager import RecordManager


class RAGManager(RecordManager):
    def __init__(self, view, request, *, name, edit_text):
        self._poll_timer, self._polling, self._epoch = None, False, 0
        self.last_job = None
        self.retry_button = Button(view.t("history_retry"), handler=self.retry)
        super().__init__(view, request, name=name, edit_text=edit_text,
                         extra_buttons=(self.retry_button,), detail_lines=3)

    def _selected_job(self):
        return next((item for item in self.entries if item["name"] == self.selected and item.get("kind") == "job"), None)

    def _phase(self, item):
        return self.view.t("rag_phase_" + item["phase"])

    def _counts(self, item):
        stats = item.get("ingestion", {})
        return self.view.t("rag_job_counts", done=stats.get("chunks_completed", 0), total=stats.get("chunks_total", 0),
                           reused=stats.get("checkpoint_reuse", 0), batches=stats.get("extraction_batches", 0))

    def hint(self):
        item = self._selected_job()
        if item is None and self.last_job and self.selected == self.last_job["label"]:
            item = self.last_job
        if item is None:
            return super().hint()
        stats = item.get("ingestion", {})
        elapsed = "—" if item.get("elapsed") is None else str(int(item["elapsed"]))
        timing = self.view.t("rag_job_times", embedding=int(stats.get("embedding_seconds", 0)),
                             extraction=int(stats.get("extraction_seconds", 0)), elapsed=elapsed)
        note = self.view.t("rag_job_retry_hint" if item.get("retryable") else "rag_job_readonly")
        return self._phase(item) + " · " + self._counts(item) + "\n" + timing + "\n" + (item.get("description") or note)

    def items(self):
        jobs = {item["name"]: item for item in self.entries if item.get("kind") == "job"}
        rows = [SidebarItem(row.key, row.title + " · " + self._phase(jobs[row.key]), self._counts(jobs[row.key]))
                if row.key in jobs else row for row in super().items()]
        if self.view._dialog is self and not self.busy and not self._polling and self._poll_timer is None:
            if any(item["status"] == "running" for item in jobs.values()):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                else:
                    self._poll_timer = loop.call_later(1, self._poll)
        return rows

    def _poll(self):
        self._poll_timer = None
        if self.view._dialog is not self or self.busy:
            return
        self._polling = True
        epoch = self._epoch
        def done(jobs):
            if epoch != self._epoch:
                return
            self._polling = False
            if jobs is None or self.view._dialog is not self or self.busy:
                return
            old = {item["name"] for item in self.entries if item.get("kind") == "job"}
            self.entries = [item for item in self.entries if item.get("kind") != "job"] + jobs
            if old - {item["name"] for item in jobs}:
                self.reload()  # A finished job may have published an editable document.
            get_app().invalidate()
        self.request("jobs", "", None, done)

    def dispose(self):
        self._epoch += 1
        self._polling = False
        if self._poll_timer is not None:
            self._poll_timer.cancel()
            self._poll_timer = None

    def observe(self, item, task):
        first = self.last_job is None or self.last_job["job_id"] != item["job_id"]
        self.last_job = item
        self.entries = [entry for entry in self.entries if entry["name"] != item["name"]]
        if item["status"] != "failed":
            self.entries.append(item)
        if item["status"] == "failed" and self.selected == item["name"]:
            self.selected = self.entries[0]["name"] if self.entries else ""
        elif first and item["status"] != "failed":
            self.selected = item["name"]
        stats = item.get("ingestion", {})
        total = stats.get("chunks_total", 0) if item["phase"] == "embedding" else None
        self.view.progress.update(task, description="RAG · " + self._phase(item),
                                  completed=stats.get("chunks_completed", 0), total=total,
                                  indeterminate=not total)
        get_app().invalidate()

    def call(self, action, argument="", version=None, completed=None, **kwargs):
        if action in ("import", "retry"):
            self.dispose()
            kwargs["observe"] = self.observe
        super().call(action, argument, version, completed, **kwargs)

    def retry(self):
        if self.busy:
            return
        item = self._selected_job()
        if item is None or not item.get("retryable"):
            self.status = self.view.t("rag_retry_unavailable")
            self.view.notice = self.status
            get_app().invalidate()
            return
        self.call("retry", item["job_id"])

    def open_item(self):
        if self._selected_job() is None:
            super().open_item()

    def delete(self):
        if self._selected_job() is None:
            super().delete()
