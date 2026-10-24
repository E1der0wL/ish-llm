"""재시작 가능한 문서 색인 작업. 입력·준비 결과·공개 영수증을 별도로 저장한다."""

from .prompts import ExtractionOptions

from .ingestion_records import EmbeddingEvidence, BatchCheckpointName, PreparationCallbacks

from llm.core.plans import PlanBlockerCode

from llm.core.checkpoints import CheckpointErrorCode
from .status import RAGJobStatus
import asyncio
from contextlib import contextmanager
import os
from uuid import uuid4

from llm.components.base import validate_name
from llm.core.models import now
from llm.core.checkpoints import ResumeAbandonment, require_checkpoint_plan, CheckpointError, CheckpointAction
from llm.core.contracts import ResourceRef, Diagnostic
from llm.core.plans import CheckpointPlan, JobResumePlan
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import revision_token, tree_snapshot, check_revision, SnapshotFile
from .job_storage import JobStorage, JobBatchReceipt, JobDocumentBatch
from .job_records import JobRecord, JobProgress, JobFailure
from .job_views import JobPlanEvidence, JobCleanupRow, JobCleanupPlan
from .job_policy import JobAdmission
from .chunk_records import RAGDocument, RAGDocumentInput, SplitDocument


class RAGJobs:
    """실행은 호출자가 await한다. 큐는 영속적이며 백엔드 시작만으로 유료 모델을 호출하지 않는다."""

    MESSAGE_CANCEL_AND_AWAIT_THE_ACTIVE_JOB_WORKER_FIRST = 'Cancel and await the active Job worker first'
    MESSAGE_ONLY_AN_INACTIVE_FAILED_OR_INTERRUPTED_JOB_CAN_RESUME = 'Only an inactive failed or interrupted Job can resume'
    MESSAGE_JOB_PRUNING_REQUIRES_EXPLICIT_IDENTIFIERS = 'Job pruning requires explicit identifiers'
    MESSAGE_JOB_PRUNING_TARGETS_MUST_BE_UNIQUE = 'Job pruning targets must be unique'
    MESSAGE_JOB_PRUNING_REQUIRES_A_REVIEWED_PLAN_VERSION = 'Job pruning requires a reviewed plan version'
    MESSAGE_JOB_RESUME_WAS_ABANDONED = 'Job resume was abandoned'
    MESSAGE_JOB_WAS_CANCELLED = 'Job was cancelled'
    MESSAGE_RAG_CONFIGURATION_CHANGED__ENQUEUE_A_NEW_JOB = 'RAG configuration changed; enqueue a new Job'
    MESSAGE_PREPARED_DOCUMENT_IDENTITY_DIFFERS_FROM_JOB_INPUT = 'Prepared document identity differs from Job input'
    MESSAGE_PREPARED_BATCH_CONFIGURATION_CHANGED__ENQUEUE_A_NEW_JOB = 'Prepared batch configuration changed; enqueue a new job'
    MESSAGE_RETRY_MUST_BE_A_BOOLEAN = 'retry must be a boolean'
    MESSAGE_INDEXING_WORKER_IS_STILL_ALIVE__CANCEL_AND_AWAIT_IT_FIRST = 'Indexing worker is still alive; cancel and await it first'
    MESSAGE_JOB_IS_ALREADY_TERMINAL_OR_ABANDONED = 'Job is already terminal or abandoned'
    MESSAGE_ABANDON_THE_JOB_BEFORE_DELETING_IT = 'Abandon the Job before deleting it'
    MESSAGE_RAG_CONFIGURATION_DIFFERS_FROM_THE_JOB_S_PREPARATION = "RAG configuration differs from the Job's preparation"
    MESSAGE_JOB_PRUNING_CANNOT_DELETE_PROTECTED_OR_UNKNOWN_RECORDS = 'Job pruning cannot delete protected or unknown records'
    MESSAGE_REVIEW_A_FRESH_JOB_RESUME_PLAN = 'review a fresh Job resume plan'
    MESSAGE_JOB_RESUME_IS_BLOCKED = 'Job resume is blocked'
    MESSAGE_INTERRUPTED_INDEXING_JOB_REQUIRES_EXPLICIT_RETRY_TRUE = 'Interrupted indexing job requires explicit retry=True'
    MESSAGE_JOB_IS_NOT_EXECUTABLE = 'Job is not executable'
    MESSAGE_RAG_CONFIGURATION_CHANGED_SINCE_PREPARATION__ENQUEUE_A_NEW_JOB = 'RAG configuration changed since preparation; enqueue a new job'
    MESSAGE_EMBEDDING_MODEL_CHANGED_SINCE_THE_JOB_WAS_PREPARED = 'Embedding model changed since the job was prepared'
    MESSAGE_PREPARED_CHUNK_VECTOR_COUNT_MISMATCH = 'Prepared chunk/vector count mismatch'
    MESSAGE_DOCUMENT_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT = 'Document already exists with different content'
    MESSAGE_PREPARED_DOCUMENT_IDENTITY_CONFIGURATION_MISMATCH = 'Prepared document identity/configuration mismatch'

    ABSENT = None
    PROBE_LEASE = True
    ACQUIRE_LEASE = JobStorage.ACQUIRE_LEASE
    INACTIVE_WORKER = False
    FIRST_INPUT = 0
    UNCHANGED_PUBLICATION = False
    NOT_CANCELLED = False
    CANCELLED = True
    UNCLAIMED = False
    CLAIMED = True

    RESUME_NOT_CREATED = "not_created"
    RESUME_AVAILABLE = "available"
    RESUME_INCOMPATIBLE = "incompatible"
    RESUME_MISSING = "missing"
    RESUME_CORRUPT = "corrupt"


    def __init__(self, data):
        self.data, self.ownership = data, data.ownership

    def _storage(self):
        project, component = self.data._current()
        return JobStorage(project, component)

    def _path(self, identifier):
        return self._storage().path(identifier)

    def _plan(self, identifier, *, live, job=ABSENT):
        project, component = self.data._current()
        component.snapshot(project)
        job = self.get(identifier) if job is RAGJobs.ABSENT else job
        source = ResourceRef(JobRecord.KIND, identifier, project_id=project.id, component=self.data.name)
        files = tree_snapshot(self._path(identifier), exclude=(JobStorage.LEASE,))
        abandoned = job[JobRecord.Field.RESUME_ABANDONMENT] is not RAGJobs.ABSENT
        common = [Diagnostic(PlanBlockerCode.ACTIVE_WORKER, RAGJobs.MESSAGE_INDEXING_WORKER_IS_STILL_ALIVE__CANCEL_AND_AWAIT_IT_FIRST, source=source)] if live else []
        abandon = list(common)
        if abandoned or job[JobRecord.Field.STATUS] in (RAGJobStatus.COMPLETED.value, RAGJobStatus.CANCELLED.value):
            abandon.append(Diagnostic(PlanBlockerCode.NOT_RESUMABLE, RAGJobs.MESSAGE_JOB_IS_ALREADY_TERMINAL_OR_ABANDONED, source=source))
        delete = list(common)
        if not abandoned and job[JobRecord.Field.STATUS] not in (RAGJobStatus.COMPLETED.value, RAGJobStatus.CANCELLED.value):
            delete.append(Diagnostic(PlanBlockerCode.RESUME_REQUIRED, RAGJobs.MESSAGE_ABANDON_THE_JOB_BEFORE_DELETING_IT, source=source))
        declarations = self._storage().checkpoint_declarations(identifier)
        keys = sorted(declarations)
        return CheckpointPlan(source, keys, sum(v[SnapshotFile.BYTES] for v in files.values()), abandoned,
            abandon, delete, revision_token(JobPlanEvidence.checkpoint(source.to_dict(), job, files, declarations, live)))

    def _resume_plan(self, identifier, *, live):
        project, component = self.data._current()
        job = self.get(identifier)
        source = ResourceRef(JobRecord.KIND, identifier, project_id=project.id, component=self.data.name)
        root = self._path(identifier)
        blockers, reused = [], []
        state = RAGJobs.RESUME_NOT_CREATED if job[JobRecord.Field.STATUS] == RAGJobStatus.QUEUED.value else RAGJobs.RESUME_AVAILABLE
        files = tree_snapshot(root, exclude=(JobStorage.LEASE,))
        def block(code, message):
            blockers.append(Diagnostic(code, message, source=source))
        if live:
            block(PlanBlockerCode.ACTIVE_WORKER, RAGJobs.MESSAGE_CANCEL_AND_AWAIT_THE_ACTIVE_JOB_WORKER_FIRST)
        if job[JobRecord.Field.RESUME_ABANDONMENT] is not RAGJobs.ABSENT or job[JobRecord.Field.CANCEL_REQUESTED] or job[JobRecord.Field.STATUS] in (RAGJobStatus.COMPLETED.value, RAGJobStatus.CANCELLED.value, RAGJobStatus.QUEUED.value):
            block(PlanBlockerCode.NOT_RESUMABLE, RAGJobs.MESSAGE_ONLY_AN_INACTIVE_FAILED_OR_INTERRUPTED_JOB_CAN_RESUME)
        try:
            self._storage().read_input(identifier, job)
            reused.extend(sorted(self._storage().checkpoints(identifier)[JobStorage.CHECKPOINT_VALUES]))
            if job[JobRecord.Field.STATUS] != RAGJobStatus.QUEUED.value and job.get(JobRecord.Field.CONFIGURATION) != component._configuration_version:
                state = RAGJobs.RESUME_INCOMPATIBLE
                block(CheckpointErrorCode.CONFIGURATION, RAGJobs.MESSAGE_RAG_CONFIGURATION_DIFFERS_FROM_THE_JOB_S_PREPARATION)
        except FileNotFoundError as error:
            state = RAGJobs.RESUME_MISSING
            block(CheckpointErrorCode.MISSING, str(error))
        except (ValueError, TypeError, KeyError) as error:
            state = RAGJobs.RESUME_CORRUPT
            block(CheckpointErrorCode.CORRUPT, str(error))
        return JobResumePlan(source, state, not blockers, sorted(reused), blockers,
            revision_token(JobPlanEvidence.resume(job, files, live, component._configuration_version)),
            live, job[JobRecord.Field.RESUME_ABANDONMENT] is not RAGJobs.ABSENT)

    @workspace_locked
    def resume_plan(self, identifier):
        self.get(identifier)  # Validate identity before opening the lease.
        lease = self._lease(identifier, probe=RAGJobs.PROBE_LEASE)
        try:
            return self._resume_plan(identifier, live=lease is RAGJobs.ABSENT)
        finally:
            if lease is not RAGJobs.ABSENT:
                lease.close()

    @workspace_locked
    def enqueue(self, *, title, content, metadata=ABSENT, identifier=ABSENT, description=ABSENT):
        from .data import validate_documents
        job_id = uuid4().hex
        identifier = job_id if identifier is RAGJobs.ABSENT else identifier
        payload = RAGDocumentInput.create(title, content, metadata, identifier)
        if description is not RAGJobs.ABSENT:
            payload[RAGDocument.Field.DESCRIPTION] = description
        payload = validate_documents([payload])[self.FIRST_INPUT]
        return self._enqueue(job_id, payload, document_id=identifier)

    @workspace_locked
    def enqueue_documents(self, documents):
        from .data import validate_documents
        inputs = validate_documents(documents)
        job_id = uuid4().hex
        payload = {JobDocumentBatch.DOCUMENTS: inputs}
        return self._enqueue(job_id, payload, document_ids=[item[RAGDocumentInput.Field.IDENTIFIER] for item in inputs])

    def _enqueue(self, identifier, payload, **documents):
        job = JobRecord.queued(identifier, documents, now(), revision_token(payload))
        self._storage().create(job, payload)
        return job

    @workspace_locked
    def get(self, identifier):
        _, records = self._storage().inventory()
        if identifier not in records:
            raise FileNotFoundError(JobStorage.ERROR_DOES_NOT_EXIST)
        return records[identifier]

    @workspace_locked
    def progress(self, identifier):
        from llm.core.contracts import OperationProgress, ResourceRef
        project, _ = self.data._current()
        job = self.get(identifier)
        return OperationProgress(ResourceRef(JobRecord.KIND, identifier, project_id=project.id, component=self.data.name),
            job[JobRecord.Field.PHASE], **job[JobRecord.Field.PROGRESS], message=job.get(JobRecord.Field.ERROR, JobProgress.DEFAULT_MESSAGE))

    @workspace_locked
    def list(self):
        _, records = self._storage().inventory()
        return [records[identifier] for identifier in sorted(records)]

    @workspace_locked
    def cancel(self, identifier):
        job = self.get(identifier)
        if job[JobRecord.Field.STATUS] in (RAGJobStatus.COMPLETED.value, RAGJobStatus.CANCELLED.value):
            return job
        JobRecord.request_cancellation(job)
        self._storage().save(job)
        return job

    @workspace_locked
    def checkpoint_plan(self, identifier):
        self.get(identifier)
        lease = self._lease(identifier, probe=RAGJobs.PROBE_LEASE)
        try:
            return self._plan(identifier, live=lease is RAGJobs.ABSENT)
        finally:
            if lease is not RAGJobs.ABSENT:
                lease.close()

    @workspace_locked
    def abandon(self, identifier, *, expected_revision):
        """실행 중인 worker는 포기할 수 없다. 기존 상태와 완료된 조각은 보존한다."""
        self.get(identifier)
        lease = self._lease(identifier, probe=RAGJobs.PROBE_LEASE)
        try:
            plan = self._plan(identifier, live=lease is RAGJobs.ABSENT)
            require_checkpoint_plan(plan, expected_revision, action=CheckpointAction.ABANDON)
            job = self.get(identifier)
            ResumeAbandonment.for_request(now(), RAGJobs.ABSENT).store(job)
            self._storage().save(job)
            return job
        finally:
            if lease is not RAGJobs.ABSENT:
                lease.close()

    @workspace_locked
    def delete(self, identifier, *, expected_revision):
        """Job 전체만 삭제한다. 공개된 corpus, VectorCache와 문서는 별도 소유 자료다."""
        self.get(identifier)
        lease = self._lease(identifier, probe=RAGJobs.PROBE_LEASE)
        try:
            plan = self._plan(identifier, live=lease is RAGJobs.ABSENT)
            require_checkpoint_plan(plan, expected_revision, action=CheckpointAction.DELETE)
            self._storage().delete(identifier)
        finally:
            if lease is not RAGJobs.ABSENT:
                lease.close()

    @contextmanager
    def _cleanup_plan(self):
        """Hold all obtainable worker leases until inspection or the explicit mutation finishes."""
        project, component = self.data._current()
        component.snapshot(project)
        storage = self._storage()
        manifest, jobs = storage.inventory()
        candidates, protected, leases = [], [], []
        try:
            for identifier, job in sorted(jobs.items()):
                lease = self._lease(identifier, probe=RAGJobs.PROBE_LEASE)
                if lease is not RAGJobs.ABSENT:
                    leases.append(lease)
                checkpoint = self._plan(identifier, live=lease is RAGJobs.ABSENT, job=job)
                abandonment = job[JobRecord.Field.RESUME_ABANDONMENT]
                ended = (ResumeAbandonment.from_dict(abandonment).abandoned_at if abandonment is not RAGJobs.ABSENT
                         else job[JobRecord.Field.ENDED_AT] if JobRecord.Field.ENDED_AT in job else job[JobRecord.Field.CREATED_AT])
                row = JobCleanupRow.create(identifier, job[JobRecord.Field.STATUS], ended,
                                           checkpoint.bytes, checkpoint.revision)
                if checkpoint.can_delete:
                    candidates.append(row)
                else:
                    protected.append(JobCleanupRow.protected(row, [item.to_dict() for item in checkpoint.delete_blockers]))
            result = JobCleanupPlan.create(project.id, self.data.name,
                revision_token(JobPlanEvidence.publication(component.source_version(project))),
                revision_token(manifest), candidates, protected)
            yield JobCleanupPlan.versioned(result)
        finally:
            for lease in leases:
                lease.close()

    @workspace_locked
    def cleanup_plan(self):
        with self._cleanup_plan() as plan:
            return plan

    @workspace_locked
    def prune(self, identifiers, *, expected_version):
        if not isinstance(identifiers, (list, tuple)):
            raise ValueError(RAGJobs.MESSAGE_JOB_PRUNING_REQUIRES_EXPLICIT_IDENTIFIERS)
        for identifier in identifiers:
            validate_name(identifier)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(RAGJobs.MESSAGE_JOB_PRUNING_TARGETS_MUST_BE_UNIQUE)
        if not isinstance(expected_version, str) or not expected_version:
            raise ValueError(RAGJobs.MESSAGE_JOB_PRUNING_REQUIRES_A_REVIEWED_PLAN_VERSION)
        with self._cleanup_plan() as plan:
            evidence = dict(plan)
            evidence.pop(JobCleanupPlan.VERSION)
            check_revision(evidence, expected_version)
            eligible = {row[JobCleanupRow.JOB_ID] for row in plan[JobCleanupPlan.CANDIDATES]}
            if any(identifier not in eligible for identifier in identifiers):
                raise ValueError(RAGJobs.MESSAGE_JOB_PRUNING_CANNOT_DELETE_PROTECTED_OR_UNKNOWN_RECORDS)
            for identifier in identifiers:
                self._storage().delete(identifier)

    @workspace_locked
    def _claim(self, identifier, retry, expected_revision=ABSENT):
        job = self.get(identifier)
        if expected_revision is not RAGJobs.ABSENT:
            plan = self._resume_plan(identifier, live=RAGJobs.INACTIVE_WORKER)
            if expected_revision != plan.revision:
                raise CheckpointError(CheckpointErrorCode.CONFLICT, RAGJobs.MESSAGE_REVIEW_A_FRESH_JOB_RESUME_PLAN)
            if not plan.can_resume:
                raise CheckpointError(CheckpointErrorCode.PROTECTED, RAGJobs.MESSAGE_JOB_RESUME_IS_BLOCKED, blockers=plan.blockers)
        if job[JobRecord.Field.RESUME_ABANDONMENT] is not RAGJobs.ABSENT:
            raise ValueError(RAGJobs.MESSAGE_JOB_RESUME_WAS_ABANDONED)
        if job[JobRecord.Field.STATUS] == RAGJobStatus.RUNNING.value:
            if not retry:
                raise ValueError(RAGJobs.MESSAGE_INTERRUPTED_INDEXING_JOB_REQUIRES_EXPLICIT_RETRY_TRUE)
        elif job[JobRecord.Field.STATUS] != RAGJobStatus.QUEUED.value and not (retry and job[JobRecord.Field.STATUS] == RAGJobStatus.FAILED.value):
            raise ValueError(RAGJobs.MESSAGE_JOB_IS_NOT_EXECUTABLE)
        if job[JobRecord.Field.CANCEL_REQUESTED]:
            raise ValueError(RAGJobs.MESSAGE_JOB_WAS_CANCELLED)
        project, component = self.data._current()
        if job[JobRecord.Field.STATUS] != RAGJobStatus.QUEUED.value and job.get(JobRecord.Field.CONFIGURATION) != component._configuration_version:
            raise CheckpointError(CheckpointErrorCode.CONFIGURATION, RAGJobs.MESSAGE_RAG_CONFIGURATION_CHANGED__ENQUEUE_A_NEW_JOB)
        maximum = component._options(project).get(JobAdmission.KEY, {}).get(JobAdmission.MAX_ACTIVE)
        if maximum is not RAGJobs.ABSENT and (type(maximum) is not int or maximum < JobAdmission.MINIMUM_ACTIVE):
            raise ValueError(JobAdmission.POSITIVE_REQUIRED)
        if maximum is not RAGJobs.ABSENT:
            active = JobAdmission.INITIAL_ACTIVE
            for item in self.list():
                if item[JobRecord.Field.ID] == identifier or item[JobRecord.Field.STATUS] != RAGJobStatus.RUNNING.value:
                    continue
                lease = self._lease(item[JobRecord.Field.ID], probe=RAGJobs.PROBE_LEASE)
                if lease is RAGJobs.ABSENT:
                    active += JobAdmission.ACTIVE_INCREMENT
                else:
                    lease.close()
            if active >= maximum:
                raise ValueError(JobAdmission.CAPACITY_REACHED)
        payload = self._storage().read_input(identifier, job)
        self._storage().checkpoints(identifier)
        # Archive the previous attempt only after admission succeeds. These are
        # observations in the same Job, never receipts or permission to retry.
        JobRecord.archive_attempt(job)
        JobRecord.start_attempt(job, os.getpid(), now(), component._configuration_version)
        self._storage().save(job)
        return payload

    @workspace_locked
    def _prepared(self, identifier, document=ABSENT):
        storage = self._storage()
        if document is not RAGJobs.ABSENT:
            storage.write_checkpoint(identifier, JobStorage.PREPARED, document)
        return storage.read_checkpoint(identifier, JobStorage.PREPARED)

    @workspace_locked
    def _commit(self, identifier, document):
        job = self.get(identifier)
        if job[JobRecord.Field.CANCEL_REQUESTED]:
            raise asyncio.CancelledError()
        project, component = self.data._current()
        snapshot = component.snapshot(project)
        prepared = document[JobDocumentBatch.DOCUMENTS] if JobRecord.Field.DOCUMENT_IDS in job else [document]
        expected = job[JobRecord.Field.DOCUMENT_IDS] if JobRecord.Field.DOCUMENT_IDS in job else [job[JobRecord.Field.DOCUMENT_ID]]
        if [item[RAGDocument.Field.ID] for item in prepared] != expected:
            raise CheckpointError(CheckpointErrorCode.CORRUPT, RAGJobs.MESSAGE_PREPARED_DOCUMENT_IDENTITY_DIFFERS_FROM_JOB_INPUT)
        documents = dict(snapshot[JobDocumentBatch.DOCUMENTS])
        changed = self.UNCHANGED_PUBLICATION
        for item in prepared:
            if item.get(RAGDocument.Field.PREPARATION_CONFIGURATION) != component._configuration_version:
                raise CheckpointError(CheckpointErrorCode.CONFIGURATION, RAGJobs.MESSAGE_RAG_CONFIGURATION_CHANGED_SINCE_PREPARATION__ENQUEUE_A_NEW_JOB)
            if EmbeddingEvidence(item).profile.model != component._identity():
                raise CheckpointError(CheckpointErrorCode.CONFIGURATION, RAGJobs.MESSAGE_EMBEDDING_MODEL_CHANGED_SINCE_THE_JOB_WAS_PREPARED)
            from llm.core.embeddings import validate_vectors
            from .graph_records import GraphRecord, validate_graph
            vectors = validate_vectors(EmbeddingEvidence(item).vectors, dimensions=EmbeddingEvidence(item).profile.dimensions)
            if len(vectors) != len(item[SplitDocument.Field.CHUNKS]):
                raise ValueError(RAGJobs.MESSAGE_PREPARED_CHUNK_VECTOR_COUNT_MISMATCH)
            validate_graph(item[GraphRecord.Field.GRAPH], item[SplitDocument.Field.CHUNKS], relation_types=component.extraction_options.get(ExtractionOptions.Field.RELATION_TYPES, ()))
            existing = documents.get(item[RAGDocument.Field.ID])
            # Publication may have succeeded before the completion receipt failed.
            if existing is not RAGJobs.ABSENT and existing != item:
                raise ValueError(RAGJobs.MESSAGE_DOCUMENT_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT)
            changed |= existing is RAGJobs.ABSENT
            documents[item[RAGDocument.Field.ID]] = item
        if changed:
            from .component import RAGComponent
            options = PreparationCallbacks.publish(lambda phase: self._telemetry(identifier, {JobRecord.Field.PHASE: phase})) if type(component).publish is RAGComponent.publish else {}
            component.publish(project, snapshot, documents, **options)
        job = self.get(identifier)
        JobRecord.finish_publication(job, now())
        self._storage().save(job)
        return job

    @workspace_locked
    def _cleanup(self, identifier):
        # A separate transaction keeps cleanup failure outside publication success.
        self._storage().clear_checkpoints(identifier)

    @workspace_locked
    def _finish_error(self, identifier, error, cancelled):
        job = self.get(identifier)
        if job[JobRecord.Field.STATUS] != RAGJobStatus.COMPLETED.value:
            from llm.errors import stable_error_code
            code = JobFailure.CANCELLED if cancelled else stable_error_code(error) or JobFailure.FAILED
            project, _ = self.data._current()
            diagnostic = Diagnostic.from_exception(error, code=code, boundary=JobFailure.BOUNDARY,
                source=ResourceRef(JobRecord.KIND, identifier, project_id=project.id, component=self.data.name))
            if diagnostic.code != code:
                diagnostic = Diagnostic(code, diagnostic.message, source=diagnostic.source,
                    details={**diagnostic.details, JobFailure.OBSERVED_CODE: diagnostic.code})
            job[JobRecord.Field.FAILED_PHASE] = job[JobRecord.Field.PHASE]
            JobRecord.finish_failure(job, cancelled, code, diagnostic.to_dict(), now())
            self._storage().save(job)

    @workspace_locked
    def _telemetry(self, identifier, values):
        job = self.get(identifier)
        JobProgress.observe(job, values)
        self._storage().save(job)

    @workspace_locked
    def _lease(self, identifier, *, probe=ACQUIRE_LEASE):
        # A lease is created only with a new Job; reads/retries never recreate it.
        return self._storage().lease(identifier, probe=probe)

    @workspace_locked
    def _batch(self, identifier, key, signature, value=ABSENT):
        key = JobStorage.BATCHES + JobStorage.CHECKPOINT_SEPARATOR + validate_name(key) + JobStorage.CHECKPOINT_SUFFIX
        if self.get(identifier).get(JobRecord.Field.CANCEL_REQUESTED):
            raise asyncio.CancelledError()
        storage = self._storage()
        if value is not RAGJobs.ABSENT:
            storage.write_batch(identifier, key, signature, value)
        saved = storage.read_checkpoint(identifier, key)
        if saved is RAGJobs.ABSENT:
            return RAGJobs.ABSENT
        if saved[JobBatchReceipt.Field.SIGNATURE] != signature:
            raise CheckpointError(CheckpointErrorCode.CONFIGURATION, RAGJobs.MESSAGE_PREPARED_BATCH_CONFIGURATION_CHANGED__ENQUEUE_A_NEW_JOB)
        return saved[JobBatchReceipt.Field.VALUE]

    async def _prepare_documents(self, identifier, inputs, component):
        """One document at a time; embedding concurrency remains the selected RAG policy."""
        from .component import RAGComponent
        prepared = []
        for position, item in enumerate(inputs):
            signature = revision_token(JobDocumentBatch.signature_payload(item, component._configuration_version))
            key = BatchCheckpointName.document(position)
            document = await self.data._async_call(self._batch, identifier, key, signature)
            await self.data._async_call(self._telemetry, identifier, JobProgress.document(position, len(inputs)))
            if document is RAGJobs.ABSENT:
                await self.data._async_call(self.data.require_model_observation, component.embedding, component.extractor)
                async def progress(batch_key, batch_signature, value=RAGJobs.ABSENT):
                    return await self.data._async_call(self._batch, identifier, BatchCheckpointName.scoped(position, batch_key), batch_signature, value)
                async def telemetry(values):
                    await self.data._async_call(self._telemetry, identifier, values)
                options = PreparationCallbacks.prepare(progress, telemetry) if type(component).prepare is RAGComponent.prepare else {}
                document = await component.prepare(item[RAGDocumentInput.Field.IDENTIFIER], item[RAGDocument.Field.TITLE], item[SplitDocument.Field.CONTENT], item[RAGDocument.Field.METADATA], RAGDocument.INITIAL_REVISION, **options)
                if item.get(RAGDocument.Field.DESCRIPTION) is not RAGJobs.ABSENT:
                    document[RAGDocument.Field.DESCRIPTION] = item[RAGDocument.Field.DESCRIPTION]
                document[RAGDocument.Field.PREPARATION_CONFIGURATION] = component._configuration_version
                await self.data._async_call(self._batch, identifier, key, signature, document)
            if document[RAGDocument.Field.ID] != item[RAGDocumentInput.Field.IDENTIFIER] or document.get(RAGDocument.Field.PREPARATION_CONFIGURATION) != component._configuration_version:
                raise ValueError(RAGJobs.MESSAGE_PREPARED_DOCUMENT_IDENTITY_CONFIGURATION_MISMATCH)
            component._embedding_dimensions = EmbeddingEvidence(document).profile.dimensions
            prepared.append(document)
        await self.data._async_call(self._telemetry, identifier, JobProgress.documents_finished(len(inputs)))
        return {JobDocumentBatch.DOCUMENTS: prepared}

    async def run(self, identifier, *, retry: bool, expected_revision=ABSENT):
        if type(retry) is not bool:
            raise TypeError(RAGJobs.MESSAGE_RETRY_MUST_BE_A_BOOLEAN)
        # 잠금 획득 직후 취소되어도 descriptor를 잃지 않고 반드시 닫는다.
        pending = asyncio.create_task(self.data._async_call(self._lease, identifier))
        cancelled = self.NOT_CANCELLED
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = self.CANCELLED
        lease = pending.result()
        if cancelled:
            lease.close()
            raise asyncio.CancelledError()
        claimed = self.UNCLAIMED
        try:
            payload = await self.data._async_call(self._claim, identifier, retry, expected_revision)
            claimed = self.CLAIMED
            document = await self.data._async_call(self._prepared, identifier)
            if document is RAGJobs.ABSENT:
                component, _ = await self.data._async_call(self.data._snapshot)
                if JobDocumentBatch.DOCUMENTS in payload:
                    document = await self._prepare_documents(identifier, payload[JobDocumentBatch.DOCUMENTS], component)
                else:
                    await self.data._async_call(self.data.require_model_observation, component.embedding, component.extractor)
                    async def progress(key, signature, value=RAGJobs.ABSENT):
                        return await self.data._async_call(self._batch, identifier, key, signature, value)
                    async def telemetry(values):
                        await self.data._async_call(self._telemetry, identifier, values)
                    from .component import RAGComponent
                    options = PreparationCallbacks.prepare(progress, telemetry) if type(component).prepare is RAGComponent.prepare else {}
                    document = await component.prepare(payload[RAGDocumentInput.Field.IDENTIFIER], payload[RAGDocument.Field.TITLE], payload[SplitDocument.Field.CONTENT], payload[RAGDocument.Field.METADATA], RAGDocument.INITIAL_REVISION, **options)
                    if RAGDocument.Field.DESCRIPTION in payload:
                        document[RAGDocument.Field.DESCRIPTION] = payload[RAGDocument.Field.DESCRIPTION]
                    document[RAGDocument.Field.PREPARATION_CONFIGURATION] = component._configuration_version
                await self.data._async_call(self._prepared, identifier, document)
            result = await self.data._async_call(self._commit, identifier, document)
            try:
                await self.data._async_call(self._cleanup, identifier)
            except OSError:
                pass  # The failed cleanup rolled back; committed publication remains valid.
            return result
        except BaseException as error:
            if claimed:
                await self.data._async_call(self._finish_error, identifier, error, isinstance(error, asyncio.CancelledError))
            raise
        finally:
            lease.close()
