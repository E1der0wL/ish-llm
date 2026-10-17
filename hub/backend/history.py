"""Request history operations through the public Session/Run facades."""

import hashlib
import json

from llm.llm import CheckpointError

from .execution.turns import TURN_OPTION, TurnIndex
from .execution.stopping import display_run, reopen_questions
from ..errors.adapters import from_exception
from ..errors.contracts import ErrorOrigin
from ..errors.operations import OperationBlocked


class TurnHistory:
    def __init__(self, runtime):
        self.runtime = runtime

    async def _edit_request(self, session_id, request, run, text):
        options = dict(run.metadata.get("engine_options", {}))
        options[TURN_OPTION] = {"source": request.id}
        return await self.runtime.submit(session_id, text, run.engine, options)

    async def index(self, session_id):
        return TurnIndex(await self.runtime.sessions[session_id].aconversation())

    async def rows(self, session_id):
        if not session_id:
            return []
        runtime, rows = self.runtime, []
        session = runtime.sessions[session_id]
        index = await self.index(session_id)
        record = None
        for turn in index.turns:
            request = turn.latest
            run = await (await session.run.aload(request.run_id)).aget_data() if request.run_id else None
            if run and str(run.status) == "paused":
                record = record or await session.aget_data()
                run = display_run(record, run)
            records = [m for group in turn.active for m in group]
            assistants = [m for m in records if m.role == "assistant"]
            elapsed = (await runtime.snapshot_reader.run_display(session, run.id))[1] if run else None
            status = request.status
            if run:
                status = run.status
            elif assistants and str(status) not in ("queued", "cancelled"):
                status = assistants[-1].status
            rows.append({"id": turn.id, "request_id": request.id, "text": turn.active[0][0].content,
                         "time": turn.attempts[0][0].created_at,
                         "status": str(status),
                         "engine": run.engine if run else request.metadata.get("engine", ""), "elapsed": elapsed,
                         "response": "\n\n".join(m.content for m in assistants),
                         "run_id": request.run_id or "", "error": str(run.error or "") if run else ""})
        return rows

    async def require_idle(self, session_id):
        state = await self.runtime.sessions[session_id].run.astatus()
        if state.active_run_id or state.queued_count or session_id in self.runtime.namer.tasks:
            raise OperationBlocked("hub_operation_busy", self.runtime.t("history_busy"))

    async def retry(self, session_id, request_id, expected_run_id):
        runtime, session = self.runtime, self.runtime.sessions[session_id]
        await self.require_idle(session_id)
        turn = (await self.index(session_id)).by_id[request_id]
        if not expected_run_id or turn.latest.run_id != expected_run_id:
            raise OperationBlocked("hub_operation_changed", runtime.t("history_changed"))
        handle = await session.run.aload(expected_run_id)
        run = await handle.aget_data()
        status = str(run.status)
        if status == "paused":
            status = str(display_run(await session.aget_data(), run).status)
        if status not in ("interrupted", "failed"):
            raise OperationBlocked("hub_not_resumable", runtime.t("history_retry_unavailable"))
        if str(run.status) == "paused" and await reopen_questions(
                session, handle, unavailable=runtime.t("history_retry_question_unavailable")):
            runtime.snapshot_reader.invalidate(session_id)
            runtime.dirty.set()
            return turn.latest.id
        for attempt in range(2):
            # Only admission's explicit revision conflict is safe to re-plan.
            # This neither executes Tools nor changes the selected source Run.
            plan = await session.run.resume_plan(run.id, engine=run.engine)
            if any(item.code == "interaction_required" for item in plan.blockers) and await reopen_questions(
                    session, handle, unavailable=runtime.t("history_retry_question_unavailable")):
                runtime.snapshot_reader.invalidate(session_id)
                runtime.dirty.set()
                return turn.latest.id
            try:
                if plan.can_resume:
                    request = await session.run.resume(run.id, engine=run.engine, partial_output=True,
                                                       expected_revision=plan.revision)
                elif plan.can_restart:
                    request = await session.run.restart(run.id, engine=run.engine, expected_revision=plan.revision)
                else:
                    details = "\n".join(dict.fromkeys(item.message for item in (*plan.blockers, *plan.restart_blockers)))
                    raise CheckpointError("checkpoint_protected", runtime.t("history_retry_blocked", details=details),
                                          blockers=(*plan.blockers, *plan.restart_blockers))
            except CheckpointError as error:
                if error.code != "checkpoint_conflict" or attempt:
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
            raise OperationBlocked("hub_operation_changed", runtime.t("history_changed"))
        if not latest.run_id:
            raise OperationBlocked("hub_edit_unavailable", runtime.t("history_edit_unavailable"))
        run = await (await session.run.aload(latest.run_id)).aget_data()
        if str(run.status) not in ("completed", "interrupted", "failed", "cancelled", "paused"):
            raise OperationBlocked("hub_edit_unavailable", runtime.t("history_edit_unavailable"))
        return await self._edit_request(session_id, latest, run, text)

    async def deletion_plan(self, session_id, identifiers):
        session = self.runtime.sessions[session_id]
        index = await self.index(session_id)
        state = await session.run.astatus()
        busy = bool(state.active_run_id or state.queued_count)
        items, blockers, seen = [], {}, set()
        for identifier in dict.fromkeys(identifiers):
            turn = index.by_id[identifier]
            if turn.id in seen:
                continue
            seen.add(turn.id)
            # Queue cancellation only affects the selected queued attempt.
            if str(turn.latest.status) == "queued" and not turn.latest.run_id:
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
            raise OperationBlocked("hub_operation_changed", runtime.t("history_delete_changed"))
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
                        raise OperationBlocked("hub_operation_changed", runtime.t("history_delete_changed"))
                    await runtime.delete_turn(session_id, item["id"], blockers, current["revision"])
                removed.append(item["id"])
            except (ValueError, KeyError, RuntimeError) as error:
                errors.append(from_exception(error, origin=ErrorOrigin.BACKEND, operation="delete_turn"))
            if progress:
                progress(count, total)
        return {"removed": removed, "errors": errors}
