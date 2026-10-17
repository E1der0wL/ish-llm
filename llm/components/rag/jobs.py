"""재시작 가능한 문서 색인 작업. 입력·준비 결과·공개 영수증을 별도로 저장한다."""

import asyncio
import os
from uuid import uuid4

from llm.components.base import validate_name
from llm.core.models import now
from llm.core.checkpoints import ResumeAbandonment, require_checkpoint_plan, CheckpointError
from llm.core.contracts import ResourceRef, Diagnostic
from llm.core.plans import CheckpointPlan, JobResumePlan
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import atomic_json, read_json, revision_token, remove_named_tree, tree_snapshot


class RAGJobs:
    """실행은 호출자가 await한다. 큐는 영속적이며 백엔드 시작만으로 유료 모델을 호출하지 않는다."""

    def __init__(self, data):
        self.data, self.ownership = data, data.ownership

    def _path(self, identifier):
        project, component = self.data._current()
        return component._checked(component.root(project) / "jobs" / validate_name(identifier))

    def _plan(self, identifier, *, live, job=None):
        project, _ = self.data._current()
        job = self.get(identifier) if job is None else job
        source = ResourceRef("rag_job", identifier, project_id=project.id, component=self.data.name)
        files = tree_snapshot(self._path(identifier), exclude=("worker.lock",))
        abandoned = "resume_abandonment" in job
        common = [Diagnostic("active_worker", "Indexing worker is still alive; cancel and await it first", source=source)] if live else []
        abandon = list(common)
        if abandoned or job["status"] in ("completed", "cancelled"):
            abandon.append(Diagnostic("not_resumable", "Job is already terminal or abandoned", source=source))
        delete = list(common)
        if not abandoned and job["status"] not in ("completed", "cancelled"):
            delete.append(Diagnostic("resume_required", "Abandon the Job before deleting it", source=source))
        keys = sorted(key for key in files if key == "prepared.json" or key.startswith("batches/"))
        return CheckpointPlan(source, keys, sum(v["bytes"] for v in files.values()), abandoned,
            abandon, delete, revision_token({"source": source.to_dict(), "job": job, "files": files, "live": live}))

    def _resume_plan(self, identifier, *, live):
        project, component = self.data._current()
        job = self.get(identifier)
        source = ResourceRef("rag_job", identifier, project_id=project.id, component=self.data.name)
        root = self._path(identifier)
        blockers, reused = [], []
        state = "not_created" if job["status"] == "queued" else "available"
        files = tree_snapshot(root, exclude=("worker.lock",))
        def block(code, message):
            blockers.append(Diagnostic(code, message, source=source))
        if live:
            block("active_worker", "Cancel and await the active Job worker first")
        if "resume_abandonment" in job or job.get("cancel_requested") or job["status"] in ("completed", "cancelled", "queued"):
            block("not_resumable", "Only an inactive failed or interrupted Job can resume")
        try:
            payload = read_json(root / "input.json")
            if revision_token(payload) != job["input_digest"]:
                raise ValueError("Job input changed")
            for key in files:
                if key == "prepared.json" or key.startswith("batches/"):
                    saved = read_json(root / key)
                    if not isinstance(saved, dict):
                        raise ValueError("Invalid Job checkpoint")
                    if key.startswith("batches/") and (set(saved) != {"signature", "value"} or not isinstance(saved["signature"], str)):
                        raise ValueError("Invalid batch checkpoint")
                    reused.append(key)
            if job["status"] != "queued" and job.get("configuration") != component._configuration_version:
                state = "incompatible"
                block("checkpoint_configuration", "RAG configuration differs from the Job's preparation")
        except FileNotFoundError as error:
            state = "missing"
            block("checkpoint_missing", str(error))
        except (ValueError, TypeError, KeyError) as error:
            state = "corrupt"
            block("checkpoint_corrupt", str(error))
        return JobResumePlan(source, state, not blockers, sorted(reused), blockers,
            revision_token({"job": job, "files": files, "live": live, "configuration": component._configuration_version}),
            live, "resume_abandonment" in job)

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
        if description is not None and not isinstance(description, str):
            raise ValueError("Document description must be text")
        job_id = uuid4().hex
        identifier = validate_name(identifier or job_id)
        payload = {"title": title, "content": content, "metadata": metadata or {}, "identifier": identifier}
        if description is not None:
            payload["description"] = description
        root = self._path(job_id)
        atomic_json(root / "input.json", payload)
        job = {"id": job_id, "document_id": identifier, "status": "queued", "created_at": now(),
               "input_digest": revision_token(payload), "phase": "queued"}
        atomic_json(root / "job.json", job)
        return job

    @workspace_locked
    def enqueue_documents(self, documents):
        from .data import validate_documents
        inputs = validate_documents(documents)
        job_id = uuid4().hex
        payload = {"documents": inputs}
        root = self._path(job_id)
        atomic_json(root / "input.json", payload)
        job = {"id": job_id, "document_ids": [item["identifier"] for item in inputs],
               "status": "queued", "created_at": now(), "input_digest": revision_token(payload), "phase": "queued"}
        atomic_json(root / "job.json", job)
        return job

    @workspace_locked
    def get(self, identifier):
        job = read_json(self._path(identifier) / "job.json")
        if job["id"] != identifier or job["status"] not in ("queued", "running", "completed", "failed", "cancelled"):
            raise ValueError("Invalid RAG job identity or status")
        if "resume_abandonment" in job:
            ResumeAbandonment.from_dict(job["resume_abandonment"])
        return job

    @workspace_locked
    def progress(self, identifier):
        from llm.core.contracts import OperationProgress, ResourceRef
        project, _ = self.data._current()
        job = self.get(identifier)
        return OperationProgress(ResourceRef("rag_job", identifier, project_id=project.id, component=self.data.name),
            job["phase"], **job.get("progress", {}), message=job.get("error", ""))

    @workspace_locked
    def list(self):
        project, component = self.data._current()
        root = component._checked(component.root(project) / "jobs")
        return [read_json(component._checked(p)) for p in sorted(root.glob("*/job.json"))]

    @workspace_locked
    def cancel(self, identifier):
        job = self.get(identifier)
        if job["status"] in ("completed", "cancelled"):
            return job
        job["cancel_requested"] = True
        if job["status"] != "running":
            job["status"] = "cancelled"
            job["phase"] = "cancelled"
        atomic_json(self._path(identifier) / "job.json", job)
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
            atomic_json(self._path(identifier) / "job.json", job)
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
            root = self._path(identifier)
            remove_named_tree(root.parent, root, identifier)
        finally:
            if lease is not None:
                lease.close()

    @workspace_locked
    def _claim(self, identifier, retry, expected_revision=None):
        root = self._path(identifier)
        job = self.get(identifier)
        if expected_revision is not None:
            plan = self._resume_plan(identifier, live=False)
            if expected_revision != plan.revision:
                raise CheckpointError("checkpoint_conflict", "review a fresh Job resume plan")
            if not plan.can_resume:
                raise CheckpointError("checkpoint_protected", "Job resume is blocked", blockers=plan.blockers)
        if "resume_abandonment" in job:
            raise ValueError("Job resume was abandoned")
        if job["status"] == "running":
            if not retry:
                raise ValueError("Interrupted indexing job requires explicit retry=True")
        elif job["status"] != "queued" and not (retry and job["status"] == "failed"):
            raise ValueError("Job is not executable")
        if job.get("cancel_requested"):
            raise ValueError("Job was cancelled")
        project, component = self.data._current()
        if job["status"] != "queued" and job.get("configuration") != component._configuration_version:
            raise CheckpointError("checkpoint_configuration", "RAG configuration changed; enqueue a new Job")
        maximum = component._options(project).get("ingestion", {}).get("max_active")
        if maximum is not None and (type(maximum) is not int or maximum < 1):
            raise ValueError("ingestion.max_active must be positive")
        if maximum is not None:
            active = 0
            for item in self.list():
                if item["id"] == identifier or item["status"] != "running":
                    continue
                lease = self._lease(item["id"], probe=True)
                if lease is None:
                    active += 1
                else:
                    lease.close()
            if active >= maximum:
                raise ValueError("rag_capacity: indexing concurrency limit reached; job remains queued")
        payload = read_json(root / "input.json")
        if revision_token(payload) != job["input_digest"]:
            raise ValueError("Job input changed")
        # Archive the previous attempt only after admission succeeds. These are
        # observations in the same Job, never receipts or permission to retry.
        if "diagnostic" in job:
            job.setdefault("failures", []).append({key: job[key] for key in (
                "status", "started_at", "ended_at", "failed_phase", "error", "diagnostic_code", "diagnostic"
            ) if key in job})
        for key in ("error", "diagnostic_code", "diagnostic", "failed_phase", "ended_at"):
            job.pop(key, None)
        job.update(status="running", phase="preparing", progress={}, pid=os.getpid(), started_at=now(),
                   configuration=component._configuration_version)
        atomic_json(root / "job.json", job)
        return payload

    @workspace_locked
    def _prepared(self, identifier, document=None):
        root = self._path(identifier)
        if document is not None:
            atomic_json(root / "prepared.json", document)
        return read_json(root / "prepared.json") if (root / "prepared.json").exists() else None

    @workspace_locked
    def _commit(self, identifier, document):
        job = self.get(identifier)
        if job.get("cancel_requested"):
            raise asyncio.CancelledError()
        project, component = self.data._current()
        snapshot = component.snapshot(project)
        prepared = document["documents"] if "document_ids" in job else [document]
        expected = job["document_ids"] if "document_ids" in job else [job["document_id"]]
        if [item["id"] for item in prepared] != expected:
            raise CheckpointError("checkpoint_corrupt", "Prepared document identity differs from Job input")
        documents = dict(snapshot["documents"])
        changed = False
        for item in prepared:
            if item.get("preparation_configuration") != component._configuration_version:
                raise CheckpointError("checkpoint_configuration", "RAG configuration changed since preparation; enqueue a new job")
            if item["profile"]["model"] != component._identity():
                raise CheckpointError("checkpoint_configuration", "Embedding model changed since the job was prepared")
            from llm.providers.embeddings import validate_vectors
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
        job.update(status="completed", phase="completed", progress={"completed": 1, "total": 1, "unit": "job"}, ended_at=now())
        atomic_json(self._path(identifier) / "job.json", job)
        # 공개된 원문/벡터는 corpus 세대가 소유한다. 완료 뒤 준비 결과의 중복 사본을 없앤다.
        try:
            from llm.services.infrastructure.storage import unlink_file
            prepared = self._path(identifier) / "prepared.json"
            if prepared.exists():
                unlink_file(prepared)
            batches = self._path(identifier) / "batches"
            if batches.exists():
                remove_named_tree(batches.parent, batches, "batches")
        except OSError:
            pass  # 공개 성공을 정리 실패로 되돌리지 않는다.
        return job

    @workspace_locked
    def _finish_error(self, identifier, error, cancelled):
        job = self.get(identifier)
        if job["status"] != "completed":
            from llm.errors import stable_error_code
            code = "rag_cancelled" if cancelled else stable_error_code(error) or "rag_failed"
            project, _ = self.data._current()
            diagnostic = Diagnostic.from_exception(error, code=code, boundary="component",
                source=ResourceRef("rag_job", identifier, project_id=project.id, component=self.data.name))
            if diagnostic.code != code:
                diagnostic = Diagnostic(code, diagnostic.message, source=diagnostic.source,
                    details={**diagnostic.details, "observed_code": diagnostic.code})
            job["failed_phase"] = job["phase"]
            job.update(status="cancelled" if cancelled else "failed", phase="cancelled" if cancelled else "failed",
                       error=code, diagnostic_code=code, diagnostic=diagnostic.to_dict(), ended_at=now())
            atomic_json(self._path(identifier) / "job.json", job)

    @workspace_locked
    def _telemetry(self, identifier, values):
        job = self.get(identifier)
        if "phase" in values:
            job["phase"] = values["phase"]
            job["progress"] = {key: values[key] for key in ("completed", "total", "unit") if key in values}
        job["ingestion"] = {**job.get("ingestion", {}), **{key: value for key, value in values.items()
                           if key not in ("phase", "completed", "total", "unit")}}
        atomic_json(self._path(identifier) / "job.json", job)

    @workspace_locked
    def _lease(self, identifier, *, probe=False):
        # PID 재사용/같은 프로세스의 죽은 coroutine 대신 실제 OS 잠금 수명으로 판정한다.
        import fcntl
        root = self._path(identifier)
        path = root / "worker.lock"
        if path.is_symlink():
            raise ValueError("Job lease cannot follow links")
        stream = path.open("a+b")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            if probe:
                return None
            raise ValueError("Indexing worker is still alive") from None
        except BaseException:
            stream.close()
            raise
        return stream

    @workspace_locked
    def _batch(self, identifier, key, signature, value=None):
        root = self._path(identifier)
        path = root / "batches" / (validate_name(key) + ".json")
        if self.get(identifier).get("cancel_requested"):
            raise asyncio.CancelledError()
        if value is not None:
            atomic_json(path, {"signature": signature, "value": value})
        if not path.exists():
            return None
        saved = read_json(path)
        if saved["signature"] != signature:
            raise CheckpointError("checkpoint_configuration", "Prepared batch configuration changed; enqueue a new job")
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

    async def run(self, identifier, *, retry=False, expected_revision=None):
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
            return await self.data._async_call(self._commit, identifier, document)
        except BaseException as error:
            if claimed:
                await self.data._async_call(self._finish_error, identifier, error, isinstance(error, asyncio.CancelledError))
            raise
        finally:
            lease.close()
