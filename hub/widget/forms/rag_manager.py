"""RAG document editing plus resumable registration jobs in one shared popup."""

from llm.llm import RAGJobStatus
import asyncio
from pathlib import Path

from prompt_toolkit.application.current import get_app
from prompt_toolkit.widgets import Label

from ..controls import Button
from ..dialog import Dialog
from ..sidebar import SidebarItem
from .record_manager import RecordManager
from ...ui.error_presentation import operation_notice
from ...errors.contracts import ErrorInfo


class RAGManager(RecordManager):
    def __init__(self, view, request, *, name, edit_text):
        self._poll_timer, self._polling, self._epoch = None, False, 0
        self.last_job = None
        self.cancel_pending = set()
        self.retry_button = Button(view.t("history_retry"), handler=self.retry, width=10)
        self.cancel_button = Button(view.t("rag_cancel_job"), handler=self.cancel_job, width=10)
        self.delete_button = Button(view.t("delete"), handler=self.delete, width=10)
        super().__init__(view, request, name=name, edit_text=edit_text,
                         extra_buttons=(self.retry_button, self.cancel_button, self.delete_button),
                         detail_lines=4, button_width=10)

    def _selected_job(self):
        return next((item for item in self.entries if item["name"] == self.selected and item.get("kind") == "job"), None)

    def _phase(self, item):
        if item.get("job_id") in self.cancel_pending and item["status"] in (RAGJobStatus.QUEUED.value, RAGJobStatus.RUNNING.value):
            return self.view.t("rag_phase_cancelling")
        return self.view.t("rag_phase_" + item["phase"])

    def _counts(self, item):
        stats = item.get("ingestion", {})
        counts = self.view.t("rag_job_counts", done=stats.get("chunks_completed", 0), total=stats.get("chunks_total", 0),
                           reused=stats.get("checkpoint_reuse", 0), batches=stats.get("extraction_batches", 0))
        progress = item.get("progress", {})
        if progress.get("total") is not None:
            counts += " · " + self.view.t("rag_stage_counts", done=progress.get("completed", 0),
                total=progress["total"], unit=self.view.t("rag_unit_" + (progress.get("unit") or "job")))
        return counts

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
        times = item.get("phase_seconds", {})
        if times:
            timing += "\n" + self.view.t("rag_observed_times") + " · ".join(
                self.view.t("rag_phase_" + phase) + f" {int(seconds)}s" for phase, seconds in times.items())
        note = self.view.t("rag_job_retry_check" if item.get("retryable") is None else
                           "rag_job_retry_hint" if item["retryable"] else "rag_job_readonly")
        failure = item.get("failure") or item.get("observation_error")
        return self._phase(item) + " · " + self._counts(item) + "\n" + timing + "\n" + (operation_notice(failure, self.view.t) if failure else note)

    def items(self):
        jobs = {item["name"]: item for item in self.entries if item.get("kind") == "job"}
        rows = [SidebarItem(row.key, row.title + " · " + self._phase(jobs[row.key]), self._counts(jobs[row.key]))
                if row.key in jobs else row for row in super().items()]
        if self.view._dialog is self and not self.busy and not self._polling and self._poll_timer is None:
            if any(item["status"] == RAGJobStatus.RUNNING.value for item in jobs.values()):
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
        if item.get("kind") == "preparation":
            self.view.progress.update(task, description="RAG · " + self._phase(item), indeterminate=True)
            return
        first = self.last_job is None or self.last_job["job_id"] != item["job_id"]
        self.last_job = item
        self.entries = [entry for entry in self.entries if entry["name"] != item["name"]]
        if item["status"] != RAGJobStatus.COMPLETED.value:
            self.entries.append(item)
        if first:
            self.selected = item["name"]
        stats = item.get("progress", {})
        total = stats.get("total")
        self.view.progress.update(task, description="RAG · " + self._phase(item),
                                  completed=stats.get("completed") or 0, total=total,
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
        if item is None or item.get("retryable") is False:
            self.status = self.view.t("rag_retry_unavailable")
            if item and item.get("resume_blockers"):
                error = ErrorInfo(code="checkpoint_protected", operation="rag.resume",
                                  details={"blockers": item["resume_blockers"]})
                self.view.operation_diagnostics.record(self.view.project_id, error)
                self.status = operation_notice(error, self.view.t)
            self.view.notice = self.status
            get_app().invalidate()
            return
        self.call("retry", item["job_id"])

    def open_item(self):
        if self._selected_job() is None:
            super().open_item()

    def delete(self):
        item = self._selected_job()
        if item is None:
            super().delete()
            return
        if self.busy:
            return
        if item["status"] not in (RAGJobStatus.FAILED.value, RAGJobStatus.CANCELLED.value):
            self.view.notice = self.view.t("rag_delete_unavailable")
            return
        def ready(plan):
            if self.view._dialog is not self:
                return
            if not plan["allowed"]:
                self.view.notice = self.view.t("rag_delete_unavailable")
                return
            def restore(confirmed=False):
                self.view.dialogs.show(self, self.list)
                if confirmed:
                    self.call("delete_job", item["job_id"], plan["revision"])
            buttons = [Button(self.view.t("confirm"), handler=lambda: restore(True)),
                       Button(self.view.t("cancel"), handler=restore)]
            popup = Dialog(title=self.view.t("component_delete"),
                body=Label(self.view.t("rag_delete_confirm", name=item["label"], bytes=plan["bytes"])), buttons=buttons)
            popup.on_close = restore
            self.view.dialogs.show(popup, buttons[1])
        self.call("job_delete_plan", item["job_id"], completed=ready)

    def cancel_job(self):
        item = self._selected_job()
        if item is None or item["status"] not in (RAGJobStatus.QUEUED.value, RAGJobStatus.RUNNING.value):
            self.view.notice = self.view.t("rag_cancel_unavailable")
            return
        if item["job_id"] in self.cancel_pending:
            self.view.notice = self.view.t("cancel_wait")
            get_app().invalidate()
            return
        self.cancel_pending.add(item["job_id"])
        get_app().invalidate()
        app = get_app()
        def explain_delay():
            if item["job_id"] in self.cancel_pending:
                self.view.notice = self.view.t("cancel_wait")
                app.invalidate()
        monitor = asyncio.get_running_loop().call_later(5, explain_delay)
        # Cancellation must remain available while the import request is busy.
        def done(entries):
            monitor.cancel()
            self.cancel_pending.discard(item["job_id"])
            if self.view.notice == self.view.t("cancel_wait"):
                self.view.notice = ""
            if entries is not None:
                self.entries = entries
            get_app().invalidate()
        self.request("cancel_job", item["job_id"], None, done)

    def import_path(self, buffer):
        state = self.path.buffer.complete_state
        if state and state.current_completion:
            return super().import_path(buffer)
        original = self.path.text
        if self.busy or Path(original.strip()).suffix.lower() not in (".png", ".jpg", ".jpeg"):
            return super().import_path(buffer)
        def restore(mode=None):
            self.view.dialogs.show(self, self.path)
            if mode:
                def imported(entries):
                    self.entries = entries
                    if self.last_job:
                        self.selected = self.last_job["label"]
                    if self.path.text == original:
                        self.path.text = ""
                self.call("import", {"path": original.strip(), "mode": mode}, completed=imported)
        def settings():
            restore()
            def ready(result):
                if self.view._dialog is self:
                    self.view.close_dialog()
                    self.view.settings.open_component(result["component"])
            self.call("vision_settings", completed=ready)
        buttons = [Button("OCR", handler=lambda: restore("ocr")),
                   Button(self.view.t("rag_image_analysis"), handler=lambda: restore("analysis")),
                   Button(self.view.t("rag_vision_settings"), handler=settings, width=14),
                   Button(self.view.t("cancel"), handler=restore)]
        popup = Dialog(title=self.view.t("rag_image_title"), body=Label(self.view.t("rag_image_hint")), buttons=buttons)
        popup.on_close = restore
        self.view.dialogs.show(popup, buttons[0])
        return True
