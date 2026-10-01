"""재시작 가능한 문서 색인 작업. 입력·준비 결과·공개 영수증을 별도로 저장한다."""

import asyncio
import os
from uuid import uuid4

from llm.components.base import validate_name
from llm.core.models import now
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import atomic_json, read_json, revision_token, remove_named_tree


class RAGJobs:
    """실행은 호출자가 await한다. 큐는 영속적이며 백엔드 시작만으로 유료 모델을 호출하지 않는다."""

    def __init__(self, data):
        self.data, self.ownership = data, data.ownership

    def _path(self, identifier):
        project, component = self.data._current()
        return component._checked(component.root(project) / "jobs" / validate_name(identifier))

    @workspace_locked
    def enqueue(self, *, title, content, metadata=None, identifier=None):
        job_id = uuid4().hex
        identifier = validate_name(identifier or job_id)
        payload = {"title": title, "content": content, "metadata": metadata or {}, "identifier": identifier}
        root = self._path(job_id)
        atomic_json(root / "input.json", payload)
        job = {"id": job_id, "document_id": identifier, "status": "queued", "created_at": now(),
               "input_digest": revision_token(payload), "phase": "queued"}
        atomic_json(root / "job.json", job)
        return job

    @workspace_locked
    def get(self, identifier):
        return read_json(self._path(identifier) / "job.json")

    @workspace_locked
    def progress(self, identifier):
        from llm.core.contracts import OperationProgress, ResourceRef
        project, _ = self.data._current()
        job = self.get(identifier)
        return OperationProgress(ResourceRef("rag_job", identifier, project_id=project.id, component=self.data.name),
            job["phase"], message=job.get("error", ""))

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
    def _claim(self, identifier, retry):
        root = self._path(identifier)
        job = self.get(identifier)
        if job["status"] == "running":
            if not retry:
                raise ValueError("Interrupted indexing job requires explicit retry=True")
        elif job["status"] != "queued" and not (retry and job["status"] == "failed"):
            raise ValueError("Job is not executable")
        if job.get("cancel_requested"):
            raise ValueError("Job was cancelled")
        project, component = self.data._current()
        maximum = component.configuration(project).get("ingestion", {}).get("max_active")
        if maximum is not None and (type(maximum) is not int or maximum < 1):
            raise ValueError("ingestion.max_active must be positive")
        active = 0
        for item in self.list():
            if item["id"] == identifier or item["status"] != "running":
                continue
            lease = self._lease(item["id"], probe=True)
            if lease is None:
                active += 1
            else:
                lease.close()
        if maximum is not None and active >= maximum:
            raise ValueError("rag_capacity: indexing concurrency limit reached; job remains queued")
        payload = read_json(root / "input.json")
        if revision_token(payload) != job["input_digest"]:
            raise ValueError("Job input changed")
        job.update(status="running", phase="preparing", pid=os.getpid(), started_at=now())
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
        if document.get("preparation_configuration") != component._configuration_version:
            raise ValueError("RAG settings changed since preparation; enqueue a new job")
        if document["profile"]["model"] != component._identity():
            raise ValueError("Embedding model changed since the job was prepared")
        existing = snapshot["documents"].get(document["id"])
        # 공개 성공 뒤 job.json 쓰기만 실패했어도 같은 문서를 다시 임베딩/공개하지 않는다.
        if existing is not None and existing != document:
            raise ValueError("Document already exists with different content")
        if existing is None:
            component.publish(project, snapshot, {**snapshot["documents"], document["id"]: document})
        job.update(status="completed", phase="completed", ended_at=now())
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
            from llm.providers.requests import error_code
            code = error_code(error)
            job.update(status="cancelled" if cancelled else "failed", phase="cancelled" if cancelled else "failed",
                       error=code, diagnostic_code=code, ended_at=now())
            atomic_json(self._path(identifier) / "job.json", job)

    @workspace_locked
    def _telemetry(self, identifier, values):
        job = self.get(identifier)
        job["ingestion"] = values
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
            raise ValueError("Prepared batch settings changed; enqueue a new job")
        return saved["value"]

    async def run(self, identifier, *, retry=False):
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
            payload = await self.data._async_call(self._claim, identifier, retry)
            claimed = True
            document = await self.data._async_call(self._prepared, identifier)
            if document is None:
                component, _ = await self.data._async_call(self.data._snapshot)
                await self.data._async_call(self.data.require_model_observation, component.embedding, component.extractor)
                async def progress(key, signature, value=None):
                    return await self.data._async_call(self._batch, identifier, key, signature, value)
                async def telemetry(values):
                    await self.data._async_call(self._telemetry, identifier, values)
                from .component import RAGComponent
                options = {"progress": progress, "telemetry": telemetry} if type(component).prepare is RAGComponent.prepare else {}
                document = await component.prepare(payload["identifier"], payload["title"], payload["content"], payload["metadata"], 1, **options)
                document["preparation_configuration"] = component._configuration_version
                await self.data._async_call(self._prepared, identifier, document)
            return await self.data._async_call(self._commit, identifier, document)
        except BaseException as error:
            if claimed:
                await self.data._async_call(self._finish_error, identifier, error, isinstance(error, asyncio.CancelledError))
            raise
        finally:
            lease.close()
