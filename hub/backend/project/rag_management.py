"""RAG jobs, resumptions and cleanup through public component APIs only."""

from llm.services.schema import ProjectConfiguration

from hub.errors.operations import OperationCode

from llm.core.checkpoints import CheckpointErrorCode
import asyncio
from datetime import datetime, timezone
import math

from llm.providers.runtime import diagnostic_scope
from llm.llm import RAGJobStatus, CheckpointError

from ...model import RAGAction
from ...errors.adapters import from_exception
from ...errors.contracts import ErrorOrigin
from ...errors.operations import OperationBlocked
from .record_management import DocumentManagement
from .management_values import ManagementOperation
from .rag_views import RAGOperation, JobDeleteReview, JobRetentionReview, ImageImport
from llm.components.rag.job_views import JobObservation, JobCleanupPlan, JobCleanupRow
from llm.components.rag.publication_records import (
    GenerationCompactionPlan,
    GenerationRow,
)
from llm.components.vision.records import VisionImageIdentity
from .rag_progress import RegistrationProgress, job_entry, document_ids


class RAGManagement(DocumentManagement):
    MIN_JOB_RETENTION_SECONDS = 0
    BUSY = "RAG job is already active"
    CANNOT_RESUME = "RAG job cannot resume"
    DELETE_STATES = "Only failed or cancelled RAG jobs can be deleted"
    CHANGED = "RAG job changed; review deletion again"
    STILL_ACTIVE = "RAG job is still active; cancel and wait before deleting"
    INVALID_RETENTION = "Job retention age must be positive and finite"
    RETENTION_CONFLICT = "retention_conflict: review the current Job policy and sources"
    EXISTING_JOB = (
        "An unfinished RAG job already exists; select it and retry or delete it"
    )

    def __init__(
        self,
        data,
        name,
        root,
        project_id,
        active=RAGOperation.ABSENT,
        *,
        project=RAGOperation.ABSENT
    ):
        super().__init__(data, name, root)
        self.project_id, self.project = project_id, project
        self.active = {} if active is RAGOperation.ABSENT else active

    async def _run(self, identifier, *, progress):
        job = await self.data.ajob(identifier)
        key = self.project_id, identifier
        if key in self.active:
            raise OperationBlocked(OperationCode.BUSY, RAGManagement.BUSY)
        plan = (
            await self.data.aresume_plan(identifier)
            if JobObservation(job).status != RAGJobStatus.QUEUED.value
            else RAGOperation.ABSENT
        )
        if key in self.active:
            raise OperationBlocked(OperationCode.BUSY, RAGManagement.BUSY)
        if plan is not RAGOperation.ABSENT and not plan.can_resume:
            raise CheckpointError(
                CheckpointErrorCode.PROTECTED,
                RAGManagement.CANNOT_RESUME,
                blockers=plan.blockers,
            )
        observer = RegistrationProgress(job, task=asyncio.current_task())
        if plan is not RAGOperation.ABSENT:
            observer.resume(plan)
        self.active[key] = observer

        def report(entry):
            if progress:
                progress(entry)

        async def refresh():
            while RAGOperation.POLL:
                report(observer.entry())
                await asyncio.sleep(RAGOperation.RENDER_INTERVAL)

        async def poll():
            while RAGOperation.POLL:
                try:
                    observer.update(await self.data.ajob_progress(identifier))
                except Exception as error:
                    # An observer failure must not cancel an indexing operation.
                    observer.observation_error = from_exception(
                        error,
                        origin=ErrorOrigin.COMPONENT,
                        operation=RAGOperation.PROGRESS,
                    )
                    return
                await asyncio.sleep(RAGOperation.READ_INTERVAL)

        timer, reader = asyncio.create_task(refresh()), asyncio.create_task(poll())
        try:
            with diagnostic_scope(observer.observe):
                await self.data.arun_job(
                    identifier,
                    retry=plan is not RAGOperation.ABSENT,
                    expected_revision=(
                        plan.revision
                        if plan is not RAGOperation.ABSENT
                        else RAGOperation.ABSENT
                    ),
                )
        except Exception as error:
            observer.failure = from_exception(
                error, origin=ErrorOrigin.COMPONENT, operation=RAGOperation.REGISTER
            )
            raise
        finally:
            timer.cancel()
            reader.cancel()
            await asyncio.gather(
                timer, reader, return_exceptions=RAGOperation.COLLECT_ERRORS
            )
            self.active.pop(key, RAGOperation.ABSENT)
            try:
                final = await self.data.ajob(identifier)
            except Exception as error:
                # Preserve the original registration error/cancellation if a
                # second failure occurs while collecting the final observation.
                observer.observation_error = from_exception(
                    error, origin=ErrorOrigin.COMPONENT, operation=RAGOperation.PROGRESS
                )
            else:
                report(observer.entry(final))

    async def _delete_job(self, identifier, revision):
        job = await self.data.ajob(identifier)
        if JobObservation(job).status not in (
            RAGJobStatus.FAILED.value,
            RAGJobStatus.CANCELLED.value,
        ):
            raise ValueError(RAGManagement.DELETE_STATES)
        plan = await self.data.acheckpoint_plan(identifier)
        if plan.revision != revision:
            raise CheckpointError(CheckpointErrorCode.CONFLICT, RAGManagement.CHANGED)
        if not plan.can_delete:
            if not plan.can_abandon:
                raise CheckpointError(
                    CheckpointErrorCode.PROTECTED,
                    RAGManagement.STILL_ACTIVE,
                    blockers=plan.abandon_blockers,
                )
            await self.data.aabandon_job(identifier, expected_revision=plan.revision)
            plan = await self.data.acheckpoint_plan(identifier)
        await self.data.adelete_job(identifier, expected_revision=plan.revision)

    async def compact_retired(self):
        """Explicit Application policy: discard all currently eligible retired generations."""
        await self.data.arecover_compaction()
        plan = await self.data.acompaction_plan()
        selected = [
            GenerationRow(row).generation
            for row in GenerationCompactionPlan(plan).candidates
        ]
        await self.data.acompact(
            selected, expected_version=GenerationCompactionPlan(plan).version
        )
        return selected

    async def plan_job_retention(self, max_age_seconds):
        """Application owns age selection; the Component supplies current deletion eligibility."""
        if max_age_seconds is not RAGOperation.ABSENT and (
            type(max_age_seconds) not in (int, float)
            or not math.isfinite(max_age_seconds)
            or max_age_seconds <= self.MIN_JOB_RETENTION_SECONDS
        ):
            raise ValueError(RAGManagement.INVALID_RETENTION)
        plan = await self.data.ajob_cleanup_plan()
        clock = datetime.now(timezone.utc)
        selected = [
            JobCleanupRow(row).identifier
            for row in JobCleanupPlan(plan).candidates
            if max_age_seconds is not RAGOperation.ABSENT
            and (
                clock - datetime.fromisoformat(JobCleanupRow(row).last_activity)
            ).total_seconds()
            > max_age_seconds
        ]
        return JobRetentionReview.create(max_age_seconds, plan, selected)

    async def apply_job_retention(self, max_age_seconds, review):
        current = await self.plan_job_retention(max_age_seconds)
        if review != current:
            raise ValueError(RAGManagement.RETENTION_CONFLICT)
        await self.data.aprune_jobs(
            JobRetentionReview(current).selected,
            expected_version=JobCleanupPlan(JobRetentionReview(current).plan).version,
        )
        return JobRetentionReview(current).selected

    async def _require_new(self, identifier):
        try:
            await self.data.aget_document(identifier)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(identifier)
        if any(
            identifier in document_ids(job)
            and JobObservation(job).status
            in (
                RAGJobStatus.QUEUED.value,
                RAGJobStatus.RUNNING.value,
                RAGJobStatus.FAILED.value,
            )
            and JobObservation(job).resume_abandonment is RAGOperation.ABSENT
            and not JobObservation(job).cancel_requested
            for job in await self.data.ajobs()
        ):
            raise OperationBlocked(
                OperationCode.EXISTING_RAG_JOB, RAGManagement.EXISTING_JOB
            )

    async def jobs(self, *, inspect=RAGOperation.INSPECT):
        entries = []
        for job in await self.data.ajobs():
            if JobObservation(job).status == RAGJobStatus.COMPLETED.value:
                continue
            observer = self.active.get(
                (self.project_id, JobObservation(job).identifier)
            )
            if observer:
                entries.append(observer.entry())
            else:
                plan = (
                    await self.data.aresume_plan(JobObservation(job).identifier)
                    if inspect
                    and JobObservation(job).status
                    in (RAGJobStatus.FAILED.value, RAGJobStatus.RUNNING.value)
                    else RAGOperation.ABSENT
                )
                entries.append(job_entry(job, plan=plan))
        return entries

    async def list(self):
        return [*await super().list(), *await self.jobs()]

    async def execute(
        self,
        action,
        argument=ManagementOperation.EMPTY_ARGUMENT,
        expected_version=RAGOperation.ABSENT,
        *,
        progress=RAGOperation.ABSENT
    ):
        if action == RAGAction.VISION_SETTINGS.value:
            record = await self.project.adescribe_config()
            view = ProjectConfiguration.view(record)
            selected = view.values.components
            if VisionImageIdentity.COMPONENT not in selected:
                await self.project.asave(
                    components=[*selected, VisionImageIdentity.COMPONENT],
                    expected_components=selected,
                    expected_version=view.config_version,
                )
            return ImageImport.vision_settings()
        if action == RAGAction.JOBS.value:
            return await self.jobs(inspect=RAGOperation.SKIP_INSPECTION)
        if action == RAGAction.JOB_DELETE_PLAN.value:
            plan = await self.data.acheckpoint_plan(argument)
            return JobDeleteReview.create(plan)
        if action == RAGAction.DELETE_JOB.value:
            await self._delete_job(argument, expected_version)
        elif action == RAGAction.CANCEL_JOB.value:
            await self.data.acancel_job(argument)
            observer = self.active.get((self.project_id, argument))
            if observer and observer.task:
                observer.task.cancel()
                await asyncio.gather(
                    observer.task, return_exceptions=RAGOperation.COLLECT_ERRORS
                )
        elif action == RAGAction.IMPORT.value:
            from .rag_import import read_document

            identifier, values = await read_document(self, argument, progress=progress)
            await self._require_new(identifier)
            job = await self.data.aenqueue_document(identifier=identifier, **values)
            if progress:
                progress(job_entry(job))
            await self._run(JobObservation(job).identifier, progress=progress)
        elif action == RAGAction.RETRY.value:
            await self._run(argument, progress=progress)
        else:
            return await super().execute(action, argument, expected_version)
        return await self.list()
