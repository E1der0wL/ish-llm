"""Request history operations through the public Session/Run facades."""

from hub.locales.keys import MessageKey

from llm.core.plans import PlanBlockerCode

from hub.errors.operations import OperationCode

from llm.core.checkpoints import CheckpointErrorCode
from llm.core.models import RunStatus, MessageStatus
from .history_values import HistoryCommand, HistoryRow, HistoryRun
from .history_deletion import TurnDeletionItem, TurnDeletionReview, TurnDeletionResult

from llm.llm import CheckpointError
from llm.services.runtime.requests import RequestAdmission

from .execution.turns import TurnIndex
from .execution.turn_values import HubTurnContext
from llm.services.history.turns import ConversationTurns
from .execution.stopping import display_run, reopen_questions
from ..errors.adapters import from_exception
from ..errors.contracts import ErrorOrigin
from ..errors.operations import OperationBlocked


class TurnHistory:
    DISPLAY_STATUS_PRIORITY = HistoryRow.DISPLAY_STATUS_PRIORITY
    ABSENT = None
    RESUME_REPLAN_ATTEMPTS = 2
    INCLUDE_PARTIAL_OUTPUT = True
    INITIAL_PROGRESS = 0
    FIRST_COMPLETED_ITEM = 1
    LINE = "\n"

    def __init__(self, runtime):
        self.runtime = runtime

    async def attempts(self, session, turn):
        """Latest admitted attempt per Agent, including a still-queued replacement."""
        selected = {}
        for group in turn.active:
            message = ConversationTurns.request(group)
            if not RequestAdmission.present(message):
                continue  # Cloned observations carry no executable request identity.
            request = await session.run.arequest(message.id)
            records = [await run.aget_data() for run in await request.aget_runs()]
            runs = {run.agent_id: run for run in records}
            for identifier in request.recipients:
                selected[identifier] = (message, runs.get(identifier))
        return dict(sorted(selected.items()))

    async def _edit_request(self, session_id, request, recipients, text):
        options = HubTurnContext.with_source(request.metadata, request.id)
        return await self.runtime._submit_recipients(
            session_id, text, recipients, options
        )

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
                if run is not self.ABSENT:
                    run = display_run(record, run)
                    _, elapsed = await runtime.snapshot_reader.run_display(
                        session, run.id
                    )
                    recipients.append(HistoryRun.from_run(identifier, run, elapsed))
                else:
                    admission = RequestAdmission.read(
                        source, project_id=session.project.id, session_id=session.id
                    )
                    recipients.append(HistoryRun.queued(identifier, source, admission))
            rows.append(HistoryRow.create(turn, request, attempts, recipients, self.DISPLAY_STATUS_PRIORITY))
        return rows

    async def require_idle(self, session_id):
        state = await self.runtime.sessions[session_id].run.astatus()
        if (
            state.active_runs
            or state.queued_count
            or session_id in self.runtime.namer.tasks
        ):
            raise OperationBlocked(
                OperationCode.BUSY, self.runtime.t(MessageKey.HISTORY_BUSY)
            )

    async def retry(self, session_id, request_id, expected_run_id):
        runtime, session = self.runtime, self.runtime.sessions[session_id]
        await self.require_idle(session_id)
        turn = (await self.index(session_id)).by_id[request_id]
        if not expected_run_id or expected_run_id not in {
            run.id
            for _, run in (await self.attempts(session, turn)).values()
            if run is not self.ABSENT
        }:
            raise OperationBlocked(
                OperationCode.CHANGED, runtime.t(MessageKey.HISTORY_CHANGED)
            )
        handle = await session.run.aload(expected_run_id)
        run = await handle.aget_data()
        status = str(run.status)
        if status == RunStatus.PAUSED:
            status = str(display_run(await session.aget_data(), run).status)
        if status not in (RunStatus.INTERRUPTED, RunStatus.FAILED):
            raise OperationBlocked(
                OperationCode.NOT_RESUMABLE,
                runtime.t(MessageKey.HISTORY_RETRY_UNAVAILABLE),
            )
        if str(run.status) == RunStatus.PAUSED and await reopen_questions(
            session,
            handle,
            unavailable=runtime.t(MessageKey.HISTORY_RETRY_QUESTION_UNAVAILABLE),
        ):
            runtime.snapshot_reader.invalidate(session_id)
            runtime.dirty.set()
            return turn.latest.id
        for attempt in range(self.RESUME_REPLAN_ATTEMPTS):
            # Only admission's explicit revision conflict is safe to re-plan.
            # This neither executes Tools nor changes the selected source Run.
            plan = await session.run.resume_plan(run.id, agent_id=run.agent_id)
            if any(
                item.code == PlanBlockerCode.INTERACTION_REQUIRED
                for item in plan.blockers
            ) and await reopen_questions(
                session,
                handle,
                unavailable=runtime.t(MessageKey.HISTORY_RETRY_QUESTION_UNAVAILABLE),
            ):
                runtime.snapshot_reader.invalidate(session_id)
                runtime.dirty.set()
                return turn.latest.id
            try:
                if plan.can_resume:
                    request = await session.run.resume(
                        run.id,
                        agent_id=run.agent_id,
                        partial_output=self.INCLUDE_PARTIAL_OUTPUT,
                        expected_revision=plan.revision,
                    )
                elif plan.can_restart:
                    request = await session.run.restart(
                        run.id, agent_id=run.agent_id, expected_revision=plan.revision
                    )
                else:
                    details = self.LINE.join(
                        dict.fromkeys(
                            item.message
                            for item in (*plan.blockers, *plan.restart_blockers)
                        )
                    )
                    raise CheckpointError(
                        CheckpointErrorCode.PROTECTED,
                        runtime.t(MessageKey.HISTORY_RETRY_BLOCKED, details=details),
                        blockers=(*plan.blockers, *plan.restart_blockers),
                    )
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
            raise OperationBlocked(
                OperationCode.CHANGED, runtime.t(MessageKey.HISTORY_CHANGED)
            )
        attempts = await self.attempts(session, turn)
        if not attempts or any(
            run is self.ABSENT
            or str(run.status)
            not in (
                RunStatus.COMPLETED,
                RunStatus.INTERRUPTED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.PAUSED,
            )
            for _, run in attempts.values()
        ):
            raise OperationBlocked(
                OperationCode.EDIT_UNAVAILABLE,
                runtime.t(MessageKey.HISTORY_EDIT_UNAVAILABLE),
            )
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
            if str(
                turn.latest.status
            ) == MessageStatus.QUEUED and not RequestAdmission.run_ids(turn.latest):
                items.append(TurnDeletionItem.cancellation(turn.latest.id))
                continue
            for group in reversed(turn.attempts):
                if busy:
                    # The public deletion planner rejects a Session containing
                    # queued work. Review dependencies after cancellation; never
                    # abandon newly discovered checkpoints without confirmation.
                    items.append(
                        TurnDeletionItem.deferred(ConversationTurns.request(group).id)
                    )
                    continue
                plan = (
                    await session.adelete_turn_plan(ConversationTurns.request(group).id)
                ).to_dict()
                items.append(
                    TurnDeletionItem.planned(ConversationTurns.request(group).id, plan)
                )
                for item in TurnDeletionReview(plan).blockers:
                    blockers[TurnDeletionReview.blocker_run_id(item)] = item
        return TurnDeletionReview.create(items, blockers)

    async def delete(
        self, session_id, identifiers, expected_revision, *, progress=ABSENT
    ):
        runtime, session = self.runtime, self.runtime.sessions[session_id]
        plan = await self.deletion_plan(session_id, identifiers)
        if TurnDeletionReview(plan).revision != expected_revision:
            raise OperationBlocked(
                OperationCode.CHANGED, runtime.t(MessageKey.HISTORY_DELETE_CHANGED)
            )
        allowed = {
            TurnDeletionReview.blocker_run_id(item)
            for item in TurnDeletionReview(plan).blockers
        }
        removed, errors = [], []
        total = len(TurnDeletionReview(plan).items)
        if progress:
            progress(self.INITIAL_PROGRESS, total)
        # Cancel queued work first so it cannot block deletion of settled turns.
        for count, item in enumerate(
            sorted(
                TurnDeletionReview(plan).items,
                key=lambda item: not TurnDeletionItem(item).queued,
            ),
            self.FIRST_COMPLETED_ITEM,
        ):
            try:
                if TurnDeletionItem(item).queued:
                    await runtime.cancel_request(session_id, TurnDeletionItem(item).id)
                else:
                    current = (
                        await session.adelete_turn_plan(TurnDeletionItem(item).id)
                    ).to_dict()
                    blockers = [
                        TurnDeletionReview.blocker_run_id(b)
                        for b in TurnDeletionReview(current).blockers
                    ]
                    if not set(blockers) <= allowed:
                        raise OperationBlocked(
                            OperationCode.CHANGED,
                            runtime.t(MessageKey.HISTORY_DELETE_CHANGED),
                        )
                    await runtime.delete_turn(
                        session_id,
                        TurnDeletionItem(item).id,
                        blockers,
                        TurnDeletionReview(current).revision,
                    )
                removed.append(TurnDeletionItem(item).id)
            except (ValueError, KeyError, RuntimeError) as error:
                errors.append(
                    from_exception(
                        error,
                        origin=ErrorOrigin.BACKEND,
                        operation=HistoryCommand.DELETE,
                    )
                )
            if progress:
                progress(count, total)
        return TurnDeletionResult.create(removed, errors)
