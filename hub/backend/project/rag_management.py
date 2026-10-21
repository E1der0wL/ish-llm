"""RAG jobs, resumptions and cleanup through public component APIs only."""

import asyncio

from llm.providers.runtime import diagnostic_scope
from llm.llm import CheckpointError

from ...errors.adapters import from_exception
from ...errors.contracts import ErrorOrigin
from ...errors.operations import OperationBlocked
from .record_management import DocumentManagement
from .rag_progress import RegistrationProgress, job_entry, document_ids


class RAGManagement(DocumentManagement):
    def __init__(self, data, name, root, project_id, active=None, *, project=None):
        super().__init__(data, name, root)
        self.project_id, self.project = project_id, project
        self.active = {} if active is None else active

    async def _run(self, identifier, *, progress):
        job = await self.data.ajob(identifier)
        key = self.project_id, identifier
        if key in self.active:
            raise OperationBlocked("hub_operation_busy", "RAG job is already active")
        plan = await self.data.aresume_plan(identifier) if job["status"] != "queued" else None
        if key in self.active:
            raise OperationBlocked("hub_operation_busy", "RAG job is already active")
        if plan is not None and not plan.can_resume:
            raise CheckpointError("checkpoint_protected", "RAG job cannot resume", blockers=plan.blockers)
        observer = RegistrationProgress(job, task=asyncio.current_task())
        self.active[key] = observer

        def report(entry):
            if progress:
                progress(entry)

        async def refresh():
            while True:
                report(observer.entry())
                await asyncio.sleep(.1)

        async def poll():
            while True:
                try:
                    observer.update(await self.data.ajob_progress(identifier))
                except Exception as error:
                    # An observer failure must not cancel an indexing operation.
                    observer.observation_error = from_exception(error, origin=ErrorOrigin.COMPONENT,
                                                                 operation="rag.progress")
                    return
                await asyncio.sleep(.25)

        timer, reader = asyncio.create_task(refresh()), asyncio.create_task(poll())
        try:
            with diagnostic_scope(observer.observe):
                await self.data.arun_job(identifier, retry=plan is not None,
                    expected_revision=plan.revision if plan is not None else None)
        except Exception as error:
            observer.failure = from_exception(error, origin=ErrorOrigin.COMPONENT, operation="rag.register")
            raise
        finally:
            timer.cancel()
            reader.cancel()
            await asyncio.gather(timer, reader, return_exceptions=True)
            self.active.pop(key, None)
            try:
                final = await self.data.ajob(identifier)
            except Exception as error:
                # Preserve the original registration error/cancellation if a
                # second failure occurs while collecting the final observation.
                observer.observation_error = from_exception(error, origin=ErrorOrigin.COMPONENT, operation="rag.progress")
            else:
                report(observer.entry(final))

    async def _delete_job(self, identifier, revision):
        job = await self.data.ajob(identifier)
        if job["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled RAG jobs can be deleted")
        plan = await self.data.acheckpoint_plan(identifier)
        if plan.revision != revision:
            raise CheckpointError("checkpoint_conflict", "RAG job changed; review deletion again")
        if not plan.can_delete:
            if not plan.can_abandon:
                raise CheckpointError("checkpoint_protected", "RAG job is still active; cancel and wait before deleting",
                                      blockers=plan.abandon_blockers)
            await self.data.aabandon_job(identifier, expected_revision=plan.revision)
            plan = await self.data.acheckpoint_plan(identifier)
        await self.data.adelete_job(identifier, expected_revision=plan.revision)

    async def _require_new(self, identifier):
        try:
            await self.data.aget_document(identifier)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(identifier)
        if any(identifier in document_ids(job) and job["status"] in ("queued", "running", "failed")
               and job["resume_abandonment"] is None and not job.get("cancel_requested")
               for job in await self.data.ajobs()):
            raise OperationBlocked("hub_existing_rag_job", "An unfinished RAG job already exists; select it and retry or delete it")

    async def jobs(self, *, inspect=True):
        entries = []
        for job in await self.data.ajobs():
            if job["status"] == "completed":
                continue
            observer = self.active.get((self.project_id, job["id"]))
            if observer:
                entries.append(observer.entry())
            else:
                plan = await self.data.aresume_plan(job["id"]) if inspect and job["status"] in ("failed", "running") else None
                entries.append(job_entry(job, plan=plan))
        return entries

    async def list(self):
        return [*await super().list(), *await self.jobs()]

    async def execute(self, action, argument="", expected_version=None, *, progress=None):
        if action == "vision_settings":
            record = await self.project.adescribe_config()
            selected = record["values"]["components"]
            if "vision" not in selected:
                await self.project.asave(components=[*selected, "vision"], expected_components=selected,
                                         expected_version=record["config_version"])
            return {"component": "vision"}
        if action == "jobs":
            return await self.jobs(inspect=False)
        if action == "job_delete_plan":
            plan = await self.data.acheckpoint_plan(argument)
            return {"revision": plan.revision, "bytes": plan.bytes,
                    "allowed": plan.can_delete or plan.can_abandon}
        if action == "delete_job":
            await self._delete_job(argument, expected_version)
        elif action == "cancel_job":
            await self.data.acancel_job(argument)
            observer = self.active.get((self.project_id, argument))
            if observer and observer.task:
                observer.task.cancel()
                await asyncio.gather(observer.task, return_exceptions=True)
        elif action == "import":
            from .rag_import import read_document
            identifier, values = await read_document(self, argument, progress=progress)
            await self._require_new(identifier)
            job = await self.data.aenqueue_document(identifier=identifier, **values)
            if progress:
                progress(job_entry(job))
            await self._run(job["id"], progress=progress)
        elif action == "retry":
            await self._run(argument, progress=progress)
        else:
            return await super().execute(action, argument, expected_version)
        return await self.list()
