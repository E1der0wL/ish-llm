"""Explicit approval decisions and continuation share one admission path."""

import asyncio
from collections import OrderedDict
from weakref import WeakValueDictionary

from llm.core.interactions import InteractionActor, InteractionOption
from llm.core.models import RunStatus, MessageStatus

from ..execution.stopping import stopped, StopIntent


class ApprovalFlow:
    RECENT_ADMISSIONS = 128

    def __init__(self, runtime):
        self.runtime = runtime
        self._locks = WeakValueDictionary()
        self._admissions = OrderedDict()

    def lock(self, session_id, run_id):
        key = (session_id, run_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def submitted(self, session_id, run_id):
        """Suppress duplicate work after durable admission, never grant permission.

        Keep the public request handle, not an assumed execution status. Canceling
        a queued continuation must let an explicit fresh review reach the backend.
        """
        key = (session_id, run_id)
        request = self._admissions.get(key)
        if request is None:
            return False
        if (await request.aget_data()).status in (MessageStatus.CANCELLED, MessageStatus.FAILED):
            self._admissions.pop(key, None)
            return False
        return True

    async def resume_approved(self, session, run, *, policy_only=False, preview=False,
                              allow_denial=False):
        """Caller holds lock; manual callers have just persisted a user decision.

        Resume validates durable decisions itself. Policy continuation and
        decline-and-continue additionally preview the native plan, excluding
        uncertain effects. An ordinary explicit approval retains its existing path.
        """
        if await self.submitted(session.id, run.id):
            return False
        current = await run.aget_data()
        allowed = (RunStatus.PAUSED,) if policy_only else StopIntent.REVIEWABLE
        if current.status not in allowed or (policy_only and stopped(await session.aget_data(), current)):
            return False
        options = {}
        if policy_only or preview:
            views = await run.ainteraction_views()
            if not views or any(
                not view.can_resume or view.response is None
                or (policy_only and view.response.actor != InteractionActor.POLICY)
                or (view.request.kind == view.request.KIND_APPROVAL
                    and (policy_only or not allow_denial) and not any(
                    option.id == view.response.option_id
                    and option.effect == InteractionOption.EFFECT_APPROVE
                    for option in view.request.options))
                for view in views
            ):
                return False
            plan = await session.run.resume_plan(current.id, agent_id=current.agent_id)
            if not plan.can_resume or plan.retry_nodes:
                return False
            options['expected_revision'] = plan.revision
        request = await session.run.resume(current.id, agent_id=current.agent_id,
            partial_output=self.runtime.PARTIAL_OUTPUT, **options)
        self._admissions[(session.id, run.id)] = request
        while len(self._admissions) > self.RECENT_ADMISSIONS:
            self._admissions.popitem(last=False)
        return True
