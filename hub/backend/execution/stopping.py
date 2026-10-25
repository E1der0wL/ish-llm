"""Hub stop intent for inactive checkpoints, using public llm operations only.

llm interrupt() targets a live worker. A paused checkpoint has no worker and
remains immutable; Hub stores its stop intent on the Session and cancels pending
interactions. Shared projections show that intent as interrupted after restart.
"""

from llm.core.models import RunStatus
import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from llm.llm import InteractionView
from llm.services.query import Query


class StopIntent:
    STOPPED = "hub_stopped_runs"
    RESUME_INTERACTIONS = "hub_resume_interactions"
    ABSENT = None
    CHANGED = True
    UNCHANGED = False
    QUEUE_PREVIEW = 0
    LATEST_COUNT = 1
    DESCENDING = True
    PENDING_ONLY = True
    REVIEWABLE = (RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED)
    RENEWABLE = (InteractionView.CANCELLED, InteractionView.EXPIRED)

    def __init__(self, metadata):
        self._metadata = metadata

    @property
    def stopped_runs(self):
        return self._metadata.get(self.STOPPED, {})

    @property
    def review_target(self):
        return self._metadata.get(self.RESUME_INTERACTIONS)

    def mark(self, run_id):
        marks = dict(self.stopped_runs)
        marks[run_id] = datetime.now(timezone.utc).isoformat()
        return {**self._metadata, self.STOPPED: marks}

    def reopen(self, run_id):
        marks = dict(self.stopped_runs)
        marks.pop(run_id, self.ABSENT)
        return {**self._metadata, self.STOPPED: marks, self.RESUME_INTERACTIONS: run_id}

    def clear_review(self):
        metadata = dict(self._metadata)
        metadata.pop(self.RESUME_INTERACTIONS)
        return metadata


STOPPED = StopIntent.STOPPED
RESUME_INTERACTIONS = StopIntent.RESUME_INTERACTIONS


def stopped(record, run):
    return bool(
        run
        and str(run.status) == RunStatus.PAUSED
        and run.id in StopIntent(record.metadata).stopped_runs
    )


def display_run(record, run):
    return (
        replace(run, status=type(run.status)(RunStatus.INTERRUPTED))
        if stopped(record, run)
        else run
    )


async def stop_paused(session):
    def stop():
        # Public facade calls share the ownership lock so admission cannot race
        # the check, interaction cancellation and Session metadata update.
        with session.app.project_manager.ownership.scope():
            state = session.run.status(queued_limit=StopIntent.QUEUE_PREVIEW)
            if state.active_runs or state.queued_count:
                return StopIntent.UNCHANGED
            handles = session.run.list(
                query=Query(
                    descending=StopIntent.DESCENDING, limit=StopIntent.LATEST_COUNT
                )
            )
            if not handles:
                return StopIntent.UNCHANGED
            handle = handles[0]
            run, record = handle.data, session.data
            if str(run.status) != RunStatus.PAUSED or stopped(record, run):
                return StopIntent.UNCHANGED
            for request in handle.interactions(pending_only=StopIntent.PENDING_ONLY):
                handle.cancel_interaction(request)
            session.save(metadata=StopIntent(record.metadata).mark(run.id))
            return StopIntent.CHANGED

    return await asyncio.to_thread(stop)


async def reopen_questions(session, handle, *, unavailable):
    """Explicit retry exposes durable interactions, never grants approval.

    Interrupted/failed tools may need an uncertain-effect decision even though
    their Run was never PAUSED. Remember the reviewed Run for the input form.
    """

    def reopen():
        with session.app.project_manager.ownership.scope():
            record, run = session.data, handle.data
            if str(run.status) not in StopIntent.REVIEWABLE:
                return StopIntent.UNCHANGED
            views = handle.interaction_views()
            actionable = [
                v for v in views if v.can_respond or v.status in StopIntent.RENEWABLE
            ]
            if not actionable:
                return StopIntent.UNCHANGED
            state = session.run.status(queued_limit=StopIntent.QUEUE_PREVIEW)
            if state.active_runs or state.queued_count:
                raise ValueError(unavailable)
            for view in actionable:
                if view.status in StopIntent.RENEWABLE:
                    handle.renew_interaction(view.request)
            session.save(metadata=StopIntent(record.metadata).reopen(run.id))
            return StopIntent.CHANGED

    return await asyncio.to_thread(reopen)


async def finish_review(session, run_id):
    """Drop the completed input-form target without overwriting other metadata."""

    def clear():
        with session.app.project_manager.ownership.scope():
            record = session.data
            intent = StopIntent(record.metadata)
            if intent.review_target == run_id:
                session.save(metadata=intent.clear_review())

    await asyncio.to_thread(clear)
