"""Hub stop intent for inactive checkpoints, using public llm operations only.

llm interrupt() targets a live worker. A paused checkpoint has no worker and
remains immutable; Hub stores its stop intent on the Session and cancels pending
interactions. Shared projections show that intent as interrupted after restart.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from llm.services.query import Query

STOPPED = "hub_stopped_runs"


def stopped(record, run):
    return bool(run and str(run.status) == "paused" and run.id in record.metadata.get(STOPPED, {}))


def display_run(record, run):
    return replace(run, status=type(run.status)("interrupted")) if stopped(record, run) else run


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
            if str(run.status) != "paused" or stopped(record, run):
                return False
            for request in handle.interactions(pending_only=True):
                handle.cancel_interaction(request)
            marks = dict(record.metadata.get(STOPPED, {}))
            marks[run.id] = datetime.now(timezone.utc).isoformat()
            session.save(metadata={**record.metadata, STOPPED: marks})
            return True
    return await asyncio.to_thread(stop)


async def reopen_questions(session, handle, *, unavailable):
    """An explicit retry restores cancelled questions, never grants approval."""
    def reopen():
        with session.app.project_manager.ownership.scope():
            record, run = session.data, handle.data
            if not stopped(record, run):
                return False
            cancelled = [v.request for v in handle.interaction_views() if v.status == "cancelled"]
            if cancelled:
                state = session.run.status(queued_limit=0)
                latest = session.run.list(query=Query(descending=True, limit=1))
                if state.active_run_id or state.queued_count or not latest or latest[0].id != run.id:
                    raise ValueError(unavailable)
            for request in cancelled:
                handle.renew_interaction(request)
            if cancelled:
                marks = dict(record.metadata[STOPPED])
                marks.pop(run.id)
                session.save(metadata={**record.metadata, STOPPED: marks})
            return bool(cancelled)
    return await asyncio.to_thread(reopen)
