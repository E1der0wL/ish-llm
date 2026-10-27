"""Continue live policy-approved pauses through the backend's resume contract."""

import asyncio
from collections import deque
from copy import deepcopy

from llm.core.agents import AgentBinding
from llm.core.interactions import InteractionActor, InteractionOption
from llm.core.models import RunStatus

from ..execution.engine_selection import HubEngineIdentity
from ..execution.stopping import stopped


class AutomaticApprovals:
    SEEN_RUNS = 128

    def __init__(self, runtime):
        self.runtime = runtime
        self.tasks = set()
        self.seen = deque(maxlen=self.SEEN_RUNS)
        self.closed = False

    async def _resume(self, source):
        project = None
        try:
            project = await self.runtime.backend.projects.aload(AgentBinding.from_run(source).project_id)
            session = await project.sessions.aload(source.session_id)
            # Publication can precede the worker's finalizer. Do not resume in
            # that callback or while a queued foreground request is executing.
            await session.run.wait_idle()
            run = await session.run.aload(source.id)
            current = await run.aget_data()
            if current.status != RunStatus.PAUSED or stopped(await session.aget_data(), current):
                return
            views = await run.ainteraction_views()
            if not views or any(
                not view.can_resume or view.response is None
                or view.response.actor != InteractionActor.POLICY
                or not any(option.id == view.response.option_id
                           and option.effect == InteractionOption.EFFECT_APPROVE
                           for option in view.request.options)
                for view in views
            ):
                return
            plan = await session.run.resume_plan(source.id, agent_id=source.agent_id)
            if not plan.can_resume or plan.retry_nodes:
                return
            await session.run.resume(source.id, agent_id=source.agent_id,
                partial_output=self.runtime.PARTIAL_OUTPUT, expected_revision=plan.revision)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if project is not None:
                await asyncio.to_thread(self.runtime.backend.services.logger.write,
                    project.data.paths.logs, 'hub.approval.resume_failed',
                    session_id=source.session_id, run_id=source.id, error=str(error))

    def observe(self, run):
        if (self.closed or run.status != RunStatus.PAUSED
                or HubEngineIdentity.internal(run.engine) or run.id in self.seen):
            return
        self.seen.append(run.id)
        task = asyncio.create_task(self._resume(deepcopy(run)))
        self.tasks.add(task)
        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)

    async def close(self):
        self.closed = True
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
