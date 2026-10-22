"""Request history operations through the public Session/Run facades."""

from llm.core.plans import PlanBlockerCode

from hub.errors.operations import OperationCode

from llm.core.checkpoints import CheckpointErrorCode
from llm.core.agents import AgentBinding
from llm.core.models import RunStatus, MessageStatus
import hashlib
import json

from llm.llm import CheckpointError
from llm.services.runtime.requests import RequestAdmission

from .execution.turns import TURN_OPTION, TurnIndex
from .execution.stopping import display_run, reopen_questions
from ..errors.adapters import from_exception
from ..errors.contracts import ErrorOrigin
from ..errors.operations import OperationBlocked


class TurnHistory:
    def __init__(self, runtime):
        self.runtime = runtime

    DISPLAY_STATUS_PRIORITY = (RunStatus.RUNNING, RunStatus.PENDING, MessageStatus.QUEUED,
        RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.CANCELLED, RunStatus.COMPLETED)

    async def attempts(self, session, turn):
        """Latest admitted attempt per Agent, including a still-queued replacement."""
        selected = {}
        for group in turn.active:
            message = group[0]
            if "admission" not in message.metadata:
                continue  # Cloned observations carry no executable request identity.
            request = await session.run.arequest(message.id)
            records = [await run.aget_data() for run in await request.aget_runs()]
            runs = {run.agent_id: run for run in records}
            for identifier in request.recipients:
                selected[identifier] = (message, runs.get(identifier))
        return dict(sorted(selected.items()))

    async def _edit_request(self, session_id, request, recipients, text):
        options = dict(request.metadata.get(AgentBinding.CONTEXT_FIELD, {}))
        options[TURN_OPTION] = {"source": request.id}
        return await self.runtime._submit_recipients(session_id, text, recipients, options)

    async def index(self, session_id):
        return TurnIndex(await self.runtime.sessions[session_id].aconversation())

    async def rows(self, session_id):
        if not session_id:
            return []
        runtime, rows = self.runtime, []
        session = runtime.sessions[session_id]
        index = await self.index(session_id)
        record = await session.aget_data()
        for turn in index.turns:
            request = turn.latest
            attempts = await self.attempts(session, turn)
            recipients = []
            for identifier, (source, run) in attempts.items():
                if run is not None:
                    run = display_run(record, run)
                    elapsed = (await runtime.snapshot_reader.run_display(session, run.id))[1]
                    recipients.append({"agent_id": identifier, "run_id": run.id, "status": str(run.status),
                        "engine": run.engine, "elapsed": elapsed, "error": str(run.error or "")})
                else:
                    admission = RequestAdmission.read(source, project_id=session.project.id, session_id=session.id)
                    recipients.append({"agent_id": identifier, "run_id": "", "status": str(source.status),
                        "engine": admission["bindings"][identifier]["engine"], "elapsed": None, "error": ""})
            records = [message for group in turn.active for message in group]
            assistants = [message for message in records if message.role == "assistant"]
            fallback = assistants[-1].status if assistants else request.status
            statuses = {item["status"] for item in recipients}
            status = next((value for value in self.DISPLAY_STATUS_PRIORITY if value in statuses), fallback)
            elapsed = [item["elapsed"] for item in recipients if item["elapsed"] is not None]
            rows.append({"id": turn.id, "request_id": request.id, "text": turn.active[0][0].content,
                         "time": turn.attempts[0][0].created_at, "status": str(status),
                         "agent_id": ", ".join(attempts), "runs": recipients,
                         "engine": ", ".join(dict.fromkeys(item["engine"] for item in recipients)),
                         "elapsed": max(elapsed) if elapsed else None,
                         "response": "\n\n".join(message.content for message in assistants),
                         "error": "\n".join(item["error"] for item in recipients if item["error"])})
        return rows

    async def require_idle(self, session_id):
        state = await self.runtime.sessions[session_id].run.astatus()
        if state.active_runs or state.queued_count or session_id in self.runtime.namer.tasks:
            raise OperationBlocked(OperationCode.BUSY, self.runtime.t("history_busy"))

    async def retry(self, session_id, request_id, expected_run_id):
        runtime, session = self.runtime, self.runtime.sessions[session_id]
        await self.require_idle(session_id)
        turn = (await self.index(session_id)).by_id[request_id]
        if not expected_run_id or expected_run_id not in {run.id for _, run in
                (await self.attempts(session, turn)).values() if run is not None}:
            raise OperationBlocked(OperationCode.CHANGED, runtime.t("history_changed"))
        handle = await session.run.aload(expected_run_id)
        run = await handle.aget_data()
        status = str(run.status)
        if status == RunStatus.PAUSED:
            status = str(display_run(await session.aget_data(), run).status)
        if status not in (RunStatus.INTERRUPTED, RunStatus.FAILED):
            raise OperationBlocked(OperationCode.NOT_RESUMABLE, runtime.t("history_retry_unavailable"))
        if str(run.status) == RunStatus.PAUSED and await reopen_questions(
                session, handle, unavailable=runtime.t("history_retry_question_unavailable")):
            runtime.snapshot_reader.invalidate(session_id)
            runtime.dirty.set()
            return turn.latest.id
        for attempt in range(2):
            # Only admission's explicit revision conflict is safe to re-plan.
            # This neither executes Tools nor changes the selected source Run.
            plan = await session.run.resume_plan(run.id, agent_id=run.agent_id)
            if any(item.code == PlanBlockerCode.INTERACTION_REQUIRED for item in plan.blockers) and await reopen_questions(
                    session, handle, unavailable=runtime.t("history_retry_question_unavailable")):
                runtime.snapshot_reader.invalidate(session_id)
                runtime.dirty.set()
                return turn.latest.id
            try:
                if plan.can_resume:
                    request = await session.run.resume(run.id, agent_id=run.agent_id, partial_output=True,
                                                       expected_revision=plan.revision)
                elif plan.can_restart:
                    request = await session.run.restart(run.id, agent_id=run.agent_id, expected_revision=plan.revision)
                else:
                    details = "\n".join(dict.fromkeys(item.message for item in (*plan.blockers, *plan.restart_blockers)))
                    raise CheckpointError(CheckpointErrorCode.PROTECTED, runtime.t("history_retry_blocked", details=details),
                                          blockers=(*plan.blockers, *plan.restart_blockers))
            except CheckpointError as error:
                if error.code != CheckpointErrorCode.CONFLICT or attempt:
                    raise
                await self.require_idle(session_id)
                continue
            break
        runtime.snapshot_reader.invalidate(session_id)
        runtime.dirty.set()
        return request.id

    async def edit(self, session_id, request_id, text, expected_request_id):
        runtime, session = self.runtime, self.runtime.sessions[session_id]
        await self.require_idle(session_id)
        turn = (await self.index(session_id)).by_id[request_id]
        latest = turn.latest
        if latest.id != expected_request_id:
            raise OperationBlocked(OperationCode.CHANGED, runtime.t("history_changed"))
        attempts = await self.attempts(session, turn)
        if not attempts or any(run is None or str(run.status) not in (
                RunStatus.COMPLETED, RunStatus.INTERRUPTED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.PAUSED)
                for _, run in attempts.values()):
            raise OperationBlocked(OperationCode.EDIT_UNAVAILABLE, runtime.t("history_edit_unavailable"))
        return await self._edit_request(session_id, latest, list(attempts), text)

    async def deletion_plan(self, session_id, identifiers):
        session = self.runtime.sessions[session_id]
        index = await self.index(session_id)
        state = await session.run.astatus()
        busy = bool(state.active_runs or state.queued_count)
        items, blockers, seen = [], {}, set()
        for identifier in dict.fromkeys(identifiers):
            turn = index.by_id[identifier]
            if turn.id in seen:
                continue
            seen.add(turn.id)
            # Queue cancellation only affects the selected queued attempt.
            if str(turn.latest.status) == MessageStatus.QUEUED and not RequestAdmission.run_ids(turn.latest):
                items.append({"id": turn.latest.id, "queued": True})
                continue
            for group in reversed(turn.attempts):
                if busy:
                    # The public deletion planner rejects a Session containing
                    # queued work. Review dependencies after cancellation; never
                    # abandon newly discovered checkpoints without confirmation.
                    items.append({"id": group[0].id, "queued": False, "deferred": True})
                    continue
                plan = (await session.adelete_turn_plan(group[0].id)).to_dict()
                items.append({"id": group[0].id, "queued": False, "plan": plan})
                for item in plan["blockers"]:
                    blockers[item["source"]["run_id"]] = item
        revision = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
        return {"items": items, "blockers": list(blockers.values()), "revision": revision}

    async def delete(self, session_id, identifiers, expected_revision, *, progress=None):
        runtime, session = self.runtime, self.runtime.sessions[session_id]
        plan = await self.deletion_plan(session_id, identifiers)
        if plan["revision"] != expected_revision:
            raise OperationBlocked(OperationCode.CHANGED, runtime.t("history_delete_changed"))
        allowed = {item["source"]["run_id"] for item in plan["blockers"]}
        removed, errors = [], []
        total = len(plan["items"])
        if progress:
            progress(0, total)
        # Cancel queued work first so it cannot block deletion of settled turns.
        for count, item in enumerate(sorted(plan["items"], key=lambda item: not item["queued"]), 1):
            try:
                if item["queued"]:
                    await runtime.cancel_request(session_id, item["id"])
                else:
                    current = (await session.adelete_turn_plan(item["id"])).to_dict()
                    blockers = [b["source"]["run_id"] for b in current["blockers"]]
                    if not set(blockers) <= allowed:
                        raise OperationBlocked(OperationCode.CHANGED, runtime.t("history_delete_changed"))
                    await runtime.delete_turn(session_id, item["id"], blockers, current["revision"])
                removed.append(item["id"])
            except (ValueError, KeyError, RuntimeError) as error:
                errors.append(from_exception(error, origin=ErrorOrigin.BACKEND, operation="delete_turn"))
            if progress:
                progress(count, total)
        return {"removed": removed, "errors": errors}
