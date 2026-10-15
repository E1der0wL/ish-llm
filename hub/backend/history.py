"""Request history operations through the public Session/Run facades."""

import hashlib
import json

from .execution.turns import TURN_OPTION, TurnIndex
from .execution.stopping import display_run, reopen_questions


class TurnHistory:
    def __init__(self, runtime):
        self.runtime = runtime

    async def _unstarted_without_checkpoint(self, handle, run, plan):
        # Missing files are not proof that earlier effects never happened.
        # Initialization, resume lineage and Steps distinguish an early stop
        # from lost/corrupt execution state. Inspect only public backend APIs.
        if (run.metadata.get("checkpoints") or "resume" in run.metadata
                or "resume_abandonment" in run.metadata
                or plan.interactions or plan.reused or plan.retry_nodes):
            return False
        strategy = self.runtime.backend.engines.resolve_request(run.engine, run.metadata.get("engine_options", {}))
        name = getattr(strategy, "checkpoint_name", None)
        if not name or not callable(getattr(strategy, "validate_resume", None)):
            return False
        try:
            await handle.acheckpoint(name)
        except FileNotFoundError:
            return not await handle.steps.alist()
        return False

    async def _restart(self, session_id, request, run, text):
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
            raise ValueError(self.runtime.t("history_busy"))

    async def retry(self, session_id, request_id, expected_run_id):
        runtime, session = self.runtime, self.runtime.sessions[session_id]
        await self.require_idle(session_id)
        turn = (await self.index(session_id)).by_id[request_id]
        if not expected_run_id or turn.latest.run_id != expected_run_id:
            raise ValueError(runtime.t("history_changed"))
        handle = await session.run.aload(expected_run_id)
        run = await handle.aget_data()
        status = str(run.status)
        if status == "paused":
            status = str(display_run(await session.aget_data(), run).status)
        if status not in ("interrupted", "failed"):
            raise ValueError(runtime.t("history_retry_unavailable"))
        if str(run.status) == "paused" and await reopen_questions(
                session, handle, unavailable=runtime.t("history_retry_question_unavailable")):
            runtime.snapshot_reader.invalidate(session_id)
            runtime.dirty.set()
            return turn.latest.id
        plan = await session.run.resume_plan(run.id, engine=run.engine)
        if not plan.can_resume:
            if await self._unstarted_without_checkpoint(handle, run, plan):
                return await self._restart(session_id, turn.latest, run, turn.latest.content)
            details = "\n".join(item.message for item in plan.blockers)
            raise ValueError(runtime.t("history_retry_blocked", details=details))
        request = await session.run.resume(run.id, engine=run.engine)
        runtime.snapshot_reader.invalidate(session_id)
        runtime.dirty.set()
        return request.id

    async def edit(self, session_id, request_id, text, expected_request_id):
        runtime, session = self.runtime, self.runtime.sessions[session_id]
        await self.require_idle(session_id)
        turn = (await self.index(session_id)).by_id[request_id]
        latest = turn.latest
        if latest.id != expected_request_id:
            raise ValueError(runtime.t("history_changed"))
        if not latest.run_id:
            raise ValueError(runtime.t("history_edit_unavailable"))
        run = await (await session.run.aload(latest.run_id)).aget_data()
        if str(run.status) not in ("completed", "interrupted", "failed", "cancelled", "paused"):
            raise ValueError(runtime.t("history_edit_unavailable"))
        return await self._restart(session_id, latest, run, text)

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
            raise ValueError(runtime.t("history_delete_changed"))
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
                        raise ValueError(runtime.t("history_delete_changed"))
                    await runtime.delete_turn(session_id, item["id"], blockers, current["revision"])
                removed.append(item["id"])
            except (ValueError, KeyError, RuntimeError) as error:
                errors.append(str(error))
            if progress:
                progress(count, total)
        return {"removed": removed, "errors": errors}
