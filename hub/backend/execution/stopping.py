"""Hub stop intent for inactive checkpoints, using public llm operations only.

llm interrupt() targets a live worker. A paused checkpoint has no worker and
remains immutable; Hub stores its stop intent on the Session and cancels pending
interactions. Shared projections show that intent as interrupted after restart.
"""

from llm.core.models import RunStatus
import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from llm.services.query import Query

STOPPED = "hub_stopped_runs"
RESUME_INTERACTIONS = "hub_resume_interactions"


def stopped(record, run):
    return bool(run and str(run.status) == RunStatus.PAUSED and run.id in record.metadata.get(STOPPED, {}))


def display_run(record, run):
    return replace(run, status=type(run.status)(RunStatus.INTERRUPTED)) if stopped(record, run) else run


async def stop_paused(session):
    def stop():
        # Public facade calls share the ownership lock so admission cannot race
        # the check, interaction cancellation and Session metadata update.
        with session.app.project_manager.ownership.scope():
            state = session.run.status(queued_limit=0)
            if state.active_run_id or state.queued_count:
                return False
            handles = session.run.list(query=Query(descending=True, limit=1))
            if not handles:
                return False
            handle = handles[0]
            run, record = handle.data, session.data
            if str(run.status) != RunStatus.PAUSED or stopped(record, run):
                return False
            for request in handle.interactions(pending_only=True):
                handle.cancel_interaction(request)
            marks = dict(record.metadata.get(STOPPED, {}))
            marks[run.id] = datetime.now(timezone.utc).isoformat()
            session.save(metadata={**record.metadata, STOPPED: marks})
            return True
    return await asyncio.to_thread(stop)


async def reopen_questions(session, handle, *, unavailable):
    """Explicit retry exposes durable interactions, never grants approval.

    Interrupted/failed tools may need an uncertain-effect decision even though
    their Run was never PAUSED. Remember the reviewed Run for the input form.
    """
    def reopen():
        with session.app.project_manager.ownership.scope():
            record, run = session.data, handle.data
            if str(run.status) not in (RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED):
                return False
            views = handle.interaction_views()
            actionable = [v for v in views if v.can_respond or v.status in ("cancelled", "expired")]
            if not actionable:
                return False
            state = session.run.status(queued_limit=0)
            if state.active_run_id or state.queued_count:
                raise ValueError(unavailable)
            for view in actionable:
                if view.status in ("cancelled", "expired"):
                    handle.renew_interaction(view.request)
            marks = dict(record.metadata.get(STOPPED, {}))
            marks.pop(run.id, None)
            session.save(metadata={**record.metadata, STOPPED: marks, RESUME_INTERACTIONS: run.id})
            return True
    return await asyncio.to_thread(reopen)


async def finish_review(session, run_id):
    """Drop the completed input-form target without overwriting other metadata."""
    def clear():
        with session.app.project_manager.ownership.scope():
            record = session.data
            if record.metadata.get(RESUME_INTERACTIONS) == run_id:
                metadata = dict(record.metadata)
                metadata.pop(RESUME_INTERACTIONS)
                session.save(metadata=metadata)
    await asyncio.to_thread(clear)
