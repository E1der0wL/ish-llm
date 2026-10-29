"""RAG document editing plus resumable registration jobs in one shared popup."""

from llm.core.checkpoints import CheckpointRejectionDetails

from hub.locales.keys import MessageKey, MessagePattern
from .manager_values import (
    ManagerView,
    ManagerCallbacks,
    DefinitionView,
    RAGManagerView,
)
from hub.backend.project.management_values import (
    ManagementAction,
    ManagementEntry,
    EditorRecord,
)
from hub.components import ManagedComponents
from hub.backend.project.rag_views import (
    RAGEntry,
    RAGOperation,
    JobDeleteReview,
    ImageImport,
)
from llm.components.rag.job_views import ProgressCounts
from llm.components.rag.ingestion_records import IngestionCounts
from llm.components.rag.job_records import JobProgress
from llm.components.vision.results import VisionExtraction


from llm.core.checkpoints import CheckpointErrorCode
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
from ...model import RAGAction


class RAGManager(RecordManager):
    def __init__(self, view, request, *, name, edit_text):
        self._poll_timer, self._polling, self._epoch = (
            ManagerView.ABSENT,
            RAGManagerView.IDLE,
            RAGManagerView.INITIAL_EPOCH,
        )
        self.last_job = ManagerView.ABSENT
        self.cancel_pending = set()
        self.retry_button = Button(
            view.t(MessageKey.HISTORY_RETRY),
            handler=self.retry,
            width=RAGManagerView.BUTTON_WIDTH,
        )
        self.cancel_button = Button(
            view.t(MessageKey.RAG_CANCEL_JOB),
            handler=self.cancel_job,
            width=RAGManagerView.BUTTON_WIDTH,
        )
        self.delete_button = Button(
            view.t(ManagerView.DELETE_TEXT),
            handler=self.delete,
            width=RAGManagerView.BUTTON_WIDTH,
        )
        super().__init__(
            view,
            request,
            name=name,
            edit_text=edit_text,
            extra_buttons=(self.retry_button, self.cancel_button, self.delete_button),
            detail_lines=RAGManagerView.DETAIL_LINES,
            button_width=RAGManagerView.BUTTON_WIDTH,
        )

    def _selected_job(self):
        return next(
            (
                item
                for item in self.entries
                if RAGEntry(item).name == self.selected
                and RAGEntry(item).kind == RAGEntry.JOB_KIND
            ),
            ManagerView.ABSENT,
        )

    def _phase(self, item):
        if RAGEntry(item).optional_job_id in self.cancel_pending and RAGEntry(
            item
        ).status in (RAGJobStatus.QUEUED.value, RAGJobStatus.RUNNING.value):
            return self.view.t(MessageKey.RAG_PHASE_CANCELLING)
        return self.view.t(MessagePattern.rag_phase(RAGEntry(item).phase))

    def _counts(self, item):
        stats = RAGEntry(item).ingestion
        counts = self.view.t(
            MessageKey.RAG_JOB_COUNTS,
            done=IngestionCounts(stats).completed,
            total=IngestionCounts(stats).total,
            reused=IngestionCounts(stats).checkpoint_reuse,
            batches=IngestionCounts(stats).extraction_batches,
        )
        progress = RAGEntry(item).progress
        if ProgressCounts(progress).optional_total is not ManagerView.ABSENT:
            counts += RAGManagerView.SEPARATOR + self.view.t(
                MessageKey.RAG_STAGE_COUNTS,
                done=ProgressCounts(progress).completed,
                total=ProgressCounts(progress).total,
                unit=self.view.t(
                    MessagePattern.rag_unit(
                        ProgressCounts(progress).optional_unit or JobProgress.UNIT
                    )
                ),
            )
        if RAGEntry(item).resume_checkpoints is not ManagerView.ABSENT:
            counts += RAGManagerView.SEPARATOR + self.view.t(
                MessageKey.RAG_RESUME_CHECKPOINTS,
                count=RAGEntry(item).resume_checkpoints,
            )
        return counts

    def hint(self):
        item = self._selected_job()
        if (
            item is ManagerView.ABSENT
            and self.last_job
            and self.selected == RAGEntry(self.last_job).required_label
        ):
            item = self.last_job
        if item is ManagerView.ABSENT:
            return super().hint()
        stats = RAGEntry(item).ingestion
        elapsed = (
            RAGManagerView.UNKNOWN
            if RAGEntry(item).elapsed is ManagerView.ABSENT
            else str(int(RAGEntry(item).required_elapsed))
        )
        timing = self.view.t(
            MessageKey.RAG_JOB_TIMES,
            embedding=int(IngestionCounts(stats).embedding_seconds),
            extraction=int(IngestionCounts(stats).extraction_seconds),
            elapsed=elapsed,
        )
        times = RAGEntry(item).phase_seconds
        if times:
            timing += (
                RAGManagerView.NEWLINE
                + self.view.t(MessageKey.RAG_OBSERVED_TIMES)
                + RAGManagerView.SEPARATOR.join(
                    self.view.t(MessagePattern.rag_phase(phase))
                    + f"{RAGManagerView.SECONDS_PREFIX}{int(seconds)}{RAGManagerView.SECONDS_SUFFIX}"
                    for phase, seconds in times.items()
                )
            )
        note = self.view.t(
            MessageKey.RAG_JOB_RETRY_CHECK
            if RAGEntry(item).retryable is ManagerView.ABSENT
            else (
                MessageKey.RAG_JOB_RETRY_HINT
                if RAGEntry(item).required_retryable
                else MessageKey.RAG_JOB_READONLY
            )
        )
        failure = RAGEntry(item).failure or RAGEntry(item).observation_error
        return (
            self._phase(item)
            + RAGManagerView.SEPARATOR
            + self._counts(item)
            + RAGManagerView.NEWLINE
            + timing
            + RAGManagerView.NEWLINE
            + (operation_notice(failure, self.view.t) if failure else note)
        )

    def items(self):
        jobs = {
            RAGEntry(item).name: item
            for item in self.entries
            if RAGEntry(item).kind == RAGEntry.JOB_KIND
        }
        rows = [
            (
                SidebarItem(
                    row.key,
                    row.title + RAGManagerView.SEPARATOR + self._phase(jobs[row.key]),
                    self._counts(jobs[row.key]),
                )
                if row.key in jobs
                else row
            )
            for row in super().items()
        ]
        if (
            self.view._dialog is self
            and not self.busy
            and not self._polling
            and self._poll_timer is ManagerView.ABSENT
        ):
            if any(
                RAGEntry(item).status == RAGJobStatus.RUNNING.value
                for item in jobs.values()
            ):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                else:
                    self._poll_timer = loop.call_later(
                        RAGManagerView.POLL_SECONDS, self._poll
                    )
        return rows

    def _poll(self):
        self._poll_timer = ManagerView.ABSENT
        if self.view._dialog is not self or self.busy:
            return
        self._polling = RAGManagerView.POLLING
        epoch = self._epoch

        def done(jobs):
            if epoch != self._epoch:
                return
            self._polling = RAGManagerView.IDLE
            if jobs is ManagerView.ABSENT or self.view._dialog is not self or self.busy:
                return
            old = {
                RAGEntry(item).name
                for item in self.entries
                if RAGEntry(item).kind == RAGEntry.JOB_KIND
            }
            self.entries = [
                item
                for item in self.entries
                if RAGEntry(item).kind != RAGEntry.JOB_KIND
            ] + jobs
            if old - {RAGEntry(item).name for item in jobs}:
                self.reload()  # A finished job may have published an editable document.
            get_app().invalidate()

        self.request(RAGAction.JOBS.value, ManagerView.EMPTY, ManagerView.ABSENT, done)

    def dispose(self):
        self._epoch += RAGManagerView.NEXT_EPOCH
        self._polling = RAGManagerView.IDLE
        if self._poll_timer is not ManagerView.ABSENT:
            self._poll_timer.cancel()
            self._poll_timer = ManagerView.ABSENT

    def observe(self, item, task):
        if RAGEntry(item).kind == RAGEntry.PREPARATION:
            self.view.progress.update(
                task,
                description=RAGManagerView.PROGRESS_PREFIX + self._phase(item),
                indeterminate=RAGManagerView.INDETERMINATE,
            )
            return
        first = (
            self.last_job is ManagerView.ABSENT
            or RAGEntry(self.last_job).job_id != RAGEntry(item).job_id
        )
        self.last_job = item
        self.entries = [
            entry
            for entry in self.entries
            if RAGEntry(entry).name != RAGEntry(item).name
        ]
        if RAGEntry(item).status != RAGJobStatus.COMPLETED.value:
            self.entries.append(item)
        if first:
            self.selected = RAGEntry(item).name
        stats = RAGEntry(item).progress
        total = ProgressCounts(stats).optional_total
        self.view.progress.update(
            task,
            description=RAGManagerView.PROGRESS_PREFIX + self._phase(item),
            completed=ProgressCounts(stats).optional_completed or RAGManagerView.ZERO,
            total=total,
            indeterminate=not total,
        )
        get_app().invalidate()

    def call(
        self,
        action,
        argument=ManagerView.EMPTY,
        version=ManagerView.ABSENT,
        completed=ManagerView.ABSENT,
        **kwargs,
    ):
        if action in (RAGAction.IMPORT.value, RAGAction.RETRY.value):
            self.dispose()
            ManagerCallbacks.observe(kwargs, self.observe)
        super().call(action, argument, version, completed, **kwargs)

    def retry(self):
        if self.busy:
            return
        item = self._selected_job()
        if item is ManagerView.ABSENT or RAGEntry(item).retryable is RAGEntry.NO_RETRY:
            self.status = self.view.t(MessageKey.RAG_RETRY_UNAVAILABLE)
            if item and RAGEntry(item).resume_blockers:
                error = ErrorInfo(
                    code=CheckpointErrorCode.PROTECTED,
                    operation=RAGOperation.RESUME,
                    details=CheckpointRejectionDetails.blocker_details(
                        RAGEntry(item).required_resume_blockers
                    ),
                )
                self.view.operation_diagnostics.record(self.view.project_id, error)
                self.status = operation_notice(error, self.view.t)
            self.view.notice = self.status
            get_app().invalidate()
            return
        self.call(RAGAction.RETRY.value, RAGEntry(item).job_id)

    def open_item(self):
        if self._selected_job() is ManagerView.ABSENT:
            super().open_item()

    def delete(self):
        item = self._selected_job()
        if item is ManagerView.ABSENT:
            super().delete()
            return
        if self.busy:
            return
        if RAGEntry(item).status not in (
            RAGJobStatus.FAILED.value,
            RAGJobStatus.CANCELLED.value,
        ):
            self.view.notice = self.view.t(MessageKey.RAG_DELETE_UNAVAILABLE)
            return

        def ready(plan):
            if self.view._dialog is not self:
                return
            if not JobDeleteReview(plan).allowed:
                self.view.notice = self.view.t(MessageKey.RAG_DELETE_UNAVAILABLE)
                return

            def restore(confirmed=ManagerView.NOT_CONFIRMED):
                self.view.dialogs.show(self, self.list)
                if confirmed:
                    self.call(
                        RAGAction.DELETE_JOB.value,
                        RAGEntry(item).job_id,
                        JobDeleteReview(plan).revision,
                    )

            buttons = [
                Button(
                    self.view.t(MessageKey.CONFIRM),
                    handler=lambda: restore(ManagerView.CONFIRMED),
                ),
                Button(self.view.t(MessageKey.CANCEL), handler=restore),
            ]
            popup = Dialog(
                title=self.view.t(MessageKey.COMPONENT_DELETE),
                body=Label(
                    self.view.t(
                        MessageKey.RAG_DELETE_CONFIRM,
                        name=RAGEntry(item).required_label,
                        bytes=JobDeleteReview(plan).bytes,
                    )
                ),
                buttons=buttons,
            )
            popup.on_close = restore
            self.view.dialogs.show(popup, buttons[RAGManagerView.CANCEL_BUTTON])

        self.call(
            RAGAction.JOB_DELETE_PLAN.value, RAGEntry(item).job_id, completed=ready
        )

    def cancel_job(self):
        item = self._selected_job()
        if item is ManagerView.ABSENT or RAGEntry(item).status not in (
            RAGJobStatus.QUEUED.value,
            RAGJobStatus.RUNNING.value,
        ):
            self.view.notice = self.view.t(MessageKey.RAG_CANCEL_UNAVAILABLE)
            return
        if RAGEntry(item).job_id in self.cancel_pending:
            self.view.notice = self.view.t(MessageKey.CANCEL_WAIT)
            get_app().invalidate()
            return
        self.cancel_pending.add(RAGEntry(item).job_id)
        get_app().invalidate()
        app = get_app()

        def explain_delay():
            if RAGEntry(item).job_id in self.cancel_pending:
                self.view.notice = self.view.t(MessageKey.CANCEL_WAIT)
                app.invalidate()

        monitor = asyncio.get_running_loop().call_later(
            RAGManagerView.CANCEL_NOTICE_SECONDS, explain_delay
        )

        # Cancellation must remain available while the import request is busy.
        def done(entries):
            monitor.cancel()
            self.cancel_pending.discard(RAGEntry(item).job_id)
            if self.view.notice == self.view.t(MessageKey.CANCEL_WAIT):
                self.view.notice = ManagerView.EMPTY
            if entries is not ManagerView.ABSENT:
                self.entries = entries
            get_app().invalidate()

        self.request(
            RAGAction.CANCEL_JOB.value, RAGEntry(item).job_id, ManagerView.ABSENT, done
        )

    def import_path(self, buffer):
        state = self.path.buffer.complete_state
        if state and state.current_completion:
            return super().import_path(buffer)
        original = self.path.text
        if (
            self.busy
            or Path(original.strip()).suffix.lower() not in ImageImport.SUFFIXES
        ):
            return super().import_path(buffer)

        def restore(mode=ManagerView.ABSENT):
            self.view.dialogs.show(self, self.path)
            if mode:

                def imported(entries):
                    self.entries = entries
                    if self.last_job:
                        self.selected = RAGEntry(self.last_job).required_label
                    if self.path.text == original:
                        self.path.text = ManagerView.EMPTY

                self.call(
                    RAGAction.IMPORT.value,
                    ImageImport.create(original.strip(), mode),
                    completed=imported,
                )

        def settings():
            restore()

            def ready(result):
                if self.view._dialog is self:
                    self.view.close_dialog()
                    self.view.settings.open_component(
                        ImageImport.settings_component(result)
                    )

            self.call(RAGAction.VISION_SETTINGS.value, completed=ready)

        buttons = [
            Button(
                ImageImport.OCR_LABEL, handler=lambda: restore(VisionExtraction.OCR)
            ),
            Button(
                self.view.t(MessageKey.RAG_IMAGE_ANALYSIS),
                handler=lambda: restore(VisionExtraction.ANALYSIS),
            ),
            Button(
                self.view.t(MessageKey.RAG_VISION_SETTINGS),
                handler=settings,
                width=RAGManagerView.VISION_BUTTON_WIDTH,
            ),
            Button(self.view.t(MessageKey.CANCEL), handler=restore),
        ]
        popup = Dialog(
            title=self.view.t(MessageKey.RAG_IMAGE_TITLE),
            body=Label(self.view.t(MessageKey.RAG_IMAGE_HINT)),
            buttons=buttons,
        )
        popup.on_close = restore
        self.view.dialogs.show(popup, buttons[RAGManagerView.PRIMARY_BUTTON])
        return ManagerView.HANDLED
