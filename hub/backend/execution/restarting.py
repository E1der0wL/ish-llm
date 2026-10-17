"""Stop Hub-owned work through public APIs before replacing its runtime."""

import asyncio

from .stopping import stop_paused


async def stop_for_restart(runtime):
    await runtime.namer.close()
    if runtime.builtin_tools:
        runtime.builtin_tools.questions.close()
    if runtime.backend is None:
        return

    async def stop(session):
        # Detach the worker first: it must not promote the next queued request
        # while cancellation of the current Run is being persisted.
        await session.run.shutdown()
        state = await session.run.astatus()
        for identifier in state.queued_request_ids:
            await (await session.run.arequest(identifier)).cancel()
        await stop_paused(session)

    sessions = [session for project in await runtime.backend.projects.alist()
                for session in await project.sessions.alist()]
    results = await asyncio.gather(*(stop(session) for session in sessions), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
