"""재시작 가능한 문서 색인 작업. 입력·준비 결과·공개 영수증을 별도로 저장한다."""

from llm.core.plans import PlanBlockerCode

from llm.core.checkpoints import CheckpointErrorCode
from .status import RAGJobStatus
import asyncio
from contextlib import contextmanager
import os
from uuid import uuid4

from llm.components.base import validate_name
from llm.core.models import now
from llm.core.checkpoints import ResumeAbandonment, require_checkpoint_plan, CheckpointError
from llm.core.contracts import ResourceRef, Diagnostic
from llm.core.plans import CheckpointPlan, JobResumePlan
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import revision_token, tree_snapshot, check_revision
from .job_storage import JobStorage


class RAGJobs:
    """실행은 호출자가 await한다. 큐는 영속적이며 백엔드 시작만으로 유료 모델을 호출하지 않는다."""

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

    def _plan(self, identifier, *, live, job=None):
        project, component = self.data._current()
        component.snapshot(project)
        job = self.get(identifier) if job is None else job
        source = ResourceRef(JobStorage.KIND, identifier, project_id=project.id, component=self.data.name)
        files = tree_snapshot(self._path(identifier), exclude=(JobStorage.LEASE,))
        abandoned = job["resume_abandonment"] is not None
        common = [Diagnostic(PlanBlockerCode.ACTIVE_WORKER, "Indexing worker is still alive; cancel and await it first", source=source)] if live else []
        abandon = list(common)
        if abandoned or job["status"] in (RAGJobStatus.COMPLETED.value, RAGJobStatus.CANCELLED.value):
            abandon.append(Diagnostic(PlanBlockerCode.NOT_RESUMABLE, "Job is already terminal or abandoned", source=source))
        delete = list(common)
        if not abandoned and job["status"] not in (RAGJobStatus.COMPLETED.value, RAGJobStatus.CANCELLED.value):
            delete.append(Diagnostic(PlanBlockerCode.RESUME_REQUIRED, "Abandon the Job before deleting it", source=source))
        declarations = self._storage().inventory()[0]["jobs"][identifier]["checkpoints"]
        keys = sorted(declarations)
        return CheckpointPlan(source, keys, sum(v["bytes"] for v in files.values()), abandoned,
            abandon, delete, revision_token({"source": source.to_dict(), "job": job, "files": files, "checkpoints": declarations, "live": live}))

    def _resume_plan(self, identifier, *, live):
        project, component = self.data._current()
        job = self.get(identifier)
        source = ResourceRef(JobStorage.KIND, identifier, project_id=project.id, component=self.data.name)
        root = self._path(identifier)
        blockers, reused = [], []
        state = RAGJobs.RESUME_NOT_CREATED if job["status"] == RAGJobStatus.QUEUED.value else RAGJobs.RESUME_AVAILABLE
        files = tree_snapshot(root, exclude=(JobStorage.LEASE,))
        def block(code, message):
            blockers.append(Diagnostic(code, message, source=source))
        if live:
            block(PlanBlockerCode.ACTIVE_WORKER, "Cancel and await the active Job worker first")
        if job["resume_abandonment"] is not None or job["cancel_requested"] or job["status"] in (RAGJobStatus.COMPLETED.value, RAGJobStatus.CANCELLED.value, RAGJobStatus.QUEUED.value):
            block(PlanBlockerCode.NOT_RESUMABLE, "Only an inactive failed or interrupted Job can resume")
        try:
            self._storage().read_input(identifier, job)
            reused.extend(sorted(self._storage().checkpoints(identifier)[1]))
            if job["status"] != RAGJobStatus.QUEUED.value and job.get("configuration") != component._configuration_version:
                state = RAGJobs.RESUME_INCOMPATIBLE
                block(CheckpointErrorCode.CONFIGURATION, "RAG configuration differs from the Job's preparation")
        except FileNotFoundError as error:
            state = RAGJobs.RESUME_MISSING
            block(CheckpointErrorCode.MISSING, str(error))
        except (ValueError, TypeError, KeyError) as error:
            state = RAGJobs.RESUME_CORRUPT
            block(CheckpointErrorCode.CORRUPT, str(error))
        return JobResumePlan(source, state, not blockers, sorted(reused), blockers,
            revision_token({"job": job, "files": files, "live": live, "configuration": component._configuration_version}),
            live, job["resume_abandonment"] is not None)

    @workspace_locked
    def resume_plan(self, identifier):
        self.get(identifier)  # Validate identity before opening the lease.
        lease = self._lease(identifier, probe=True)
        try:
            return self._resume_plan(identifier, live=lease is None)
        finally:
            if lease is not None:
                lease.close()

    @workspace_locked
    def enqueue(self, *, title, content, metadata=None, identifier=None, description=None):
        from .data import validate_documents
        job_id = uuid4().hex
        identifier = job_id if identifier is None else identifier
        payload = {"title": title, "content": content, "metadata": metadata, "identifier": identifier}
        if description is not None:
            payload["description"] = description
        payload = validate_documents([payload])[0]
        return self._enqueue(job_id, payload, document_id=identifier)

    @workspace_locked
    def enqueue_documents(self, documents):
        from .data import validate_documents
        inputs = validate_documents(documents)
        job_id = uuid4().hex
        payload = {"documents": inputs}
        return self._enqueue(job_id, payload, document_ids=[item["identifier"] for item in inputs])

    def _enqueue(self, identifier, payload, **documents):
        job = {"id": identifier, **documents, "status": RAGJobStatus.QUEUED.value, "created_at": now(),
               "input_digest": revision_token(payload), "phase": RAGJobStatus.QUEUED.value,
               "cancel_requested": False, "resume_abandonment": None, "failures": [], "progress": {}, "ingestion": {}}
        self._storage().create(job, payload)
        return job

    @workspace_locked
    def get(self, identifier):
        _, records = self._storage().inventory()
        if identifier not in records:
            raise FileNotFoundError("RAG Job does not exist")
        return records[identifier]

    @workspace_locked
    def progress(self, identifier):
        from llm.core.contracts import OperationProgress, ResourceRef
        project, _ = self.data._current()
        job = self.get(identifier)
        return OperationProgress(ResourceRef(JobStorage.KIND, identifier, project_id=project.id, component=self.data.name),
            job["phase"], **job["progress"], message=job.get("error", ""))

    @workspace_locked
    def list(self):
        _, records = self._storage().inventory()
        return [records[identifier] for identifier in sorted(records)]

    @workspace_locked
    def cancel(self, identifier):
        job = self.get(identifier)
        if job["status"] in (RAGJobStatus.COMPLETED.value, RAGJobStatus.CANCELLED.value):
            return job
        job["cancel_requested"] = True
        if job["status"] != RAGJobStatus.RUNNING.value:
            job["status"] = RAGJobStatus.CANCELLED.value
            job["phase"] = RAGJobStatus.CANCELLED.value
        self._storage().save(job)
        return job

    @workspace_locked
    def checkpoint_plan(self, identifier):
        self.get(identifier)
        lease = self._lease(identifier, probe=True)
        try:
            return self._plan(identifier, live=lease is None)
        finally:
            if lease is not None:
                lease.close()

    @workspace_locked
    def abandon(self, identifier, *, expected_revision):
        """실행 중인 worker는 포기할 수 없다. 기존 상태와 완료된 조각은 보존한다."""
        self.get(identifier)
        lease = self._lease(identifier, probe=True)
        try:
            plan = self._plan(identifier, live=lease is None)
            require_checkpoint_plan(plan, expected_revision, action="abandon")
            job = self.get(identifier)
            job["resume_abandonment"] = ResumeAbandonment(now(), "explicit").to_dict()
            self._storage().save(job)
            return job
        finally:
            if lease is not None:
                lease.close()

    @workspace_locked
    def delete(self, identifier, *, expected_revision):
        """Job 전체만 삭제한다. 공개된 corpus, VectorCache와 문서는 별도 소유 자료다."""
        self.get(identifier)
        lease = self._lease(identifier, probe=True)
        try:
            plan = self._plan(identifier, live=lease is None)
            require_checkpoint_plan(plan, expected_revision, action="delete")
            self._storage().delete(identifier)
        finally:
            if lease is not None:
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
                lease = self._lease(identifier, probe=True)
                if lease is not None:
                    leases.append(lease)
                checkpoint = self._plan(identifier, live=lease is None, job=job)
                abandonment = job['resume_abandonment']
                ended = (ResumeAbandonment.from_dict(abandonment).abandoned_at if abandonment is not None
                         else job['ended_at'] if 'ended_at' in job else job['created_at'])
                row = {'job_id': identifier, 'status': job['status'], 'last_activity': ended,
                       'bytes': checkpoint.bytes, 'revision': checkpoint.revision}
                if checkpoint.can_delete:
                    candidates.append(row)
                else:
                    protected.append({**row, 'reasons': [item.to_dict() for item in checkpoint.delete_blockers]})
            result = {'project_id': project.id, 'component': self.data.name,
                      'publication': revision_token({'source': component.source_version(project)}), 'inventory': revision_token(manifest),
                      'candidates': candidates, 'protected': protected}
            yield {**result, 'version': revision_token(result)}
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
            raise ValueError('Job pruning requires explicit identifiers')
        for identifier in identifiers:
            validate_name(identifier)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('Job pruning targets must be unique')
        if not isinstance(expected_version, str) or not expected_version:
            raise ValueError('Job pruning requires a reviewed plan version')
        with self._cleanup_plan() as plan:
            evidence = dict(plan)
            evidence.pop('version')
            check_revision(evidence, expected_version)
            eligible = {row['job_id'] for row in plan['candidates']}
            if any(identifier not in eligible for identifier in identifiers):
                raise ValueError('Job pruning cannot delete protected or unknown records')
            for identifier in identifiers:
                self._storage().delete(identifier)

    @workspace_locked
    def _claim(self, identifier, retry, expected_revision=None):
        job = self.get(identifier)
        if expected_revision is not None:
            plan = self._resume_plan(identifier, live=False)
            if expected_revision != plan.revision:
                raise CheckpointError(CheckpointErrorCode.CONFLICT, "review a fresh Job resume plan")
            if not plan.can_resume:
                raise CheckpointError(CheckpointErrorCode.PROTECTED, "Job resume is blocked", blockers=plan.blockers)
        if job["resume_abandonment"] is not None:
            raise ValueError("Job resume was abandoned")
        if job["status"] == RAGJobStatus.RUNNING.value:
            if not retry:
                raise ValueError("Interrupted indexing job requires explicit retry=True")
        elif job["status"] != RAGJobStatus.QUEUED.value and not (retry and job["status"] == RAGJobStatus.FAILED.value):
            raise ValueError("Job is not executable")
        if job["cancel_requested"]:
            raise ValueError("Job was cancelled")
        project, component = self.data._current()
        if job["status"] != RAGJobStatus.QUEUED.value and job.get("configuration") != component._configuration_version:
            raise CheckpointError(CheckpointErrorCode.CONFIGURATION, "RAG configuration changed; enqueue a new Job")
        maximum = component._options(project).get("ingestion", {}).get("max_active")
        if maximum is not None and (type(maximum) is not int or maximum < 1):
            raise ValueError("ingestion.max_active must be positive")
        if maximum is not None:
            active = 0
            for item in self.list():
                if item["id"] == identifier or item["status"] != RAGJobStatus.RUNNING.value:
                    continue
                lease = self._lease(item["id"], probe=True)
                if lease is None:
                    active += 1
                else:
                    lease.close()
            if active >= maximum:
                raise ValueError("rag_capacity: indexing concurrency limit reached; job remains queued")
        payload = self._storage().read_input(identifier, job)
        self._storage().checkpoints(identifier)
        # Archive the previous attempt only after admission succeeds. These are
        # observations in the same Job, never receipts or permission to retry.
        if "diagnostic" in job:
            job["failures"].append({key: job[key] for key in JobStorage.FAILURE_FIELDS})
        for key in JobStorage.ERROR_FIELDS | {"ended_at"}:
            job.pop(key, None)
        job.update(status=RAGJobStatus.RUNNING.value, phase="preparing", progress={}, pid=os.getpid(), started_at=now(),
                   configuration=component._configuration_version)
        self._storage().save(job)
        return payload

    @workspace_locked
    def _prepared(self, identifier, document=None):
        storage = self._storage()
        if document is not None:
            storage.write_checkpoint(identifier, JobStorage.PREPARED, document)
        return storage.read_checkpoint(identifier, JobStorage.PREPARED)

    @workspace_locked
    def _commit(self, identifier, document):
        job = self.get(identifier)
        if job["cancel_requested"]:
            raise asyncio.CancelledError()
        project, component = self.data._current()
        snapshot = component.snapshot(project)
        prepared = document["documents"] if "document_ids" in job else [document]
        expected = job["document_ids"] if "document_ids" in job else [job["document_id"]]
        if [item["id"] for item in prepared] != expected:
            raise CheckpointError(CheckpointErrorCode.CORRUPT, "Prepared document identity differs from Job input")
        documents = dict(snapshot["documents"])
        changed = False
        for item in prepared:
            if item.get("preparation_configuration") != component._configuration_version:
                raise CheckpointError(CheckpointErrorCode.CONFIGURATION, "RAG configuration changed since preparation; enqueue a new job")
            if item["profile"]["model"] != component._identity():
                raise CheckpointError(CheckpointErrorCode.CONFIGURATION, "Embedding model changed since the job was prepared")
            from llm.core.embeddings import validate_vectors
            from .extraction import validate_graph
            vectors = validate_vectors(item["vectors"], dimensions=item["profile"]["dimensions"])
            if len(vectors) != len(item["chunks"]):
                raise ValueError("Prepared chunk/vector count mismatch")
            validate_graph(item["graph"], item["chunks"], relation_types=component.extraction_options.get("relation_types", ()))
            existing = documents.get(item["id"])
            # Publication may have succeeded before the completion receipt failed.
            if existing is not None and existing != item:
                raise ValueError("Document already exists with different content")
            changed |= existing is None
            documents[item["id"]] = item
        if changed:
            from .component import RAGComponent
            options = {"progress": lambda phase: self._telemetry(identifier, {"phase": phase})} if type(component).publish is RAGComponent.publish else {}
            component.publish(project, snapshot, documents, **options)
        job = self.get(identifier)
        job.update(status=RAGJobStatus.COMPLETED.value, phase=RAGJobStatus.COMPLETED.value, progress={"completed": 1, "total": 1, "unit": "job"}, ended_at=now())
        self._storage().save(job)
        return job

    @workspace_locked
    def _cleanup(self, identifier):
        # A separate transaction keeps cleanup failure outside publication success.
        self._storage().clear_checkpoints(identifier)

    @workspace_locked
    def _finish_error(self, identifier, error, cancelled):
        job = self.get(identifier)
        if job["status"] != RAGJobStatus.COMPLETED.value:
            from llm.errors import stable_error_code
            code = "rag_cancelled" if cancelled else stable_error_code(error) or "rag_failed"
            project, _ = self.data._current()
            diagnostic = Diagnostic.from_exception(error, code=code, boundary="component",
                source=ResourceRef(JobStorage.KIND, identifier, project_id=project.id, component=self.data.name))
            if diagnostic.code != code:
                diagnostic = Diagnostic(code, diagnostic.message, source=diagnostic.source,
                    details={**diagnostic.details, "observed_code": diagnostic.code})
            job["failed_phase"] = job["phase"]
            job.update(status=RAGJobStatus.CANCELLED.value if cancelled else RAGJobStatus.FAILED.value, phase=RAGJobStatus.CANCELLED.value if cancelled else RAGJobStatus.FAILED.value,
                       error=code, diagnostic_code=code, diagnostic=diagnostic.to_dict(), ended_at=now())
            self._storage().save(job)

    @workspace_locked
    def _telemetry(self, identifier, values):
        job = self.get(identifier)
        if "phase" in values:
            job["phase"] = values["phase"]
            job["progress"] = {key: values[key] for key in ("completed", "total", "unit") if key in values}
        job["ingestion"] = {**job["ingestion"], **{key: value for key, value in values.items()
                           if key not in ("phase", "completed", "total", "unit")}}
        self._storage().save(job)

    @workspace_locked
    def _lease(self, identifier, *, probe=False):
        # A lease is created only with a new Job; reads/retries never recreate it.
        return self._storage().lease(identifier, probe=probe)

    @workspace_locked
    def _batch(self, identifier, key, signature, value=None):
        key = JobStorage.BATCHES + "/" + validate_name(key) + ".json"
        if self.get(identifier).get("cancel_requested"):
            raise asyncio.CancelledError()
        storage = self._storage()
        if value is not None:
            storage.write_batch(identifier, key, signature, value)
        saved = storage.read_checkpoint(identifier, key)
        if saved is None:
            return None
        if saved["signature"] != signature:
            raise CheckpointError(CheckpointErrorCode.CONFIGURATION, "Prepared batch configuration changed; enqueue a new job")
        return saved["value"]

    async def _prepare_documents(self, identifier, inputs, component):
        """One document at a time; embedding concurrency remains the selected RAG policy."""
        from .component import RAGComponent
        prepared = []
        for position, item in enumerate(inputs):
            signature = revision_token({"input": item, "configuration": component._configuration_version})
            key = f"document_{position}"
            document = await self.data._async_call(self._batch, identifier, key, signature)
            await self.data._async_call(self._telemetry, identifier, {"phase": "preparing_documents",
                "completed": position, "total": len(inputs), "unit": "document",
                "documents_completed": position, "documents_total": len(inputs), "document_index": position})
            if document is None:
                await self.data._async_call(self.data.require_model_observation, component.embedding, component.extractor)
                async def progress(batch_key, batch_signature, value=None):
                    return await self.data._async_call(self._batch, identifier, f"d{position}_{batch_key}", batch_signature, value)
                async def telemetry(values):
                    await self.data._async_call(self._telemetry, identifier, values)
                options = {"progress": progress, "telemetry": telemetry} if type(component).prepare is RAGComponent.prepare else {}
                document = await component.prepare(item["identifier"], item["title"], item["content"], item["metadata"], 1, **options)
                if item.get("description") is not None:
                    document["description"] = item["description"]
                document["preparation_configuration"] = component._configuration_version
                await self.data._async_call(self._batch, identifier, key, signature, document)
            if document["id"] != item["identifier"] or document.get("preparation_configuration") != component._configuration_version:
                raise ValueError("Prepared document identity/configuration mismatch")
            component._embedding_dimensions = document["profile"]["dimensions"]
            prepared.append(document)
        await self.data._async_call(self._telemetry, identifier, {"phase": "preparing_documents",
            "completed": len(inputs), "total": len(inputs), "unit": "document",
            "documents_completed": len(inputs), "documents_total": len(inputs)})
        return {"documents": prepared}

    async def run(self, identifier, *, retry: bool, expected_revision=None):
        if type(retry) is not bool:
            raise TypeError("retry must be a boolean")
        # 잠금 획득 직후 취소되어도 descriptor를 잃지 않고 반드시 닫는다.
        pending = asyncio.create_task(self.data._async_call(self._lease, identifier))
        cancelled = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
        lease = pending.result()
        if cancelled:
            lease.close()
            raise asyncio.CancelledError()
        claimed = False
        try:
            payload = await self.data._async_call(self._claim, identifier, retry, expected_revision)
            claimed = True
            document = await self.data._async_call(self._prepared, identifier)
            if document is None:
                component, _ = await self.data._async_call(self.data._snapshot)
                if "documents" in payload:
                    document = await self._prepare_documents(identifier, payload["documents"], component)
                else:
                    await self.data._async_call(self.data.require_model_observation, component.embedding, component.extractor)
                    async def progress(key, signature, value=None):
                        return await self.data._async_call(self._batch, identifier, key, signature, value)
                    async def telemetry(values):
                        await self.data._async_call(self._telemetry, identifier, values)
                    from .component import RAGComponent
                    options = {"progress": progress, "telemetry": telemetry} if type(component).prepare is RAGComponent.prepare else {}
                    document = await component.prepare(payload["identifier"], payload["title"], payload["content"], payload["metadata"], 1, **options)
                    if "description" in payload:
                        document["description"] = payload["description"]
                    document["preparation_configuration"] = component._configuration_version
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
