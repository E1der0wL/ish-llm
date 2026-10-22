"""Actual process termination at recipient admission, dispatch and effect boundaries."""

import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from llm.core.models import RunStatus
from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel
from llm.services.infrastructure.transactions import after_commit
from llm.services.runtime.runs import RunRepository


async def main(root, boundary):
    root.mkdir(parents=True, exist_ok=True)
    case = {}

    def evidence(name, value):
        case[name] = value
        with (root / "case.json").open("w") as stream:
            json.dump(case, stream)
            stream.flush()
            os.fsync(stream.fileno())

    def stop():
        evidence("reached", boundary)
        os._exit(91)

    async def action(context):
        if context.run.agent_id == "b" and boundary in ("after_effect", "after_finish"):
            # Select the crash victim explicitly; parallel Agent start order is
            # not a promise. The sibling remains active without another effect.
            await asyncio.Event().wait()
        with (root / "effects.txt").open("a") as stream:
            stream.write(context.run.agent_id + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if boundary == "after_effect":
            stop()
        yield context.run.agent_id

    async with LargeLanguageModel(root / "workspace", engines={"work": BaseEngine(action=action)}) as app:
        project = await app.projects.acreate()
        for identifier in ("a", "b"):
            await project.agents.acreate({"engine": "work"}, identifier=identifier)
        session = await project.sessions.acreate(agent_ids=["a", "b"])
        evidence("project_id", project.id)
        evidence("session_id", session.id)
        manager = app._manager(session._snapshot)
        admit = manager._admit
        dispatch = manager._dispatch_request
        create, save = RunRepository.create, RunRepository.save
        written = []

        def admitted(*args, **kwargs):
            message = admit(*args, **kwargs)
            evidence("request_id", message.id)
            if boundary == "before_ack":
                stop()
            return message

        def created(repository, run):
            create(repository, run)
            written.append(run.id)
            evidence("created_runs", written)
            if boundary == "partial_dispatch" and len(written) == 1:
                stop()

        def dispatched(*args, **kwargs):
            runs = dispatch(*args, **kwargs)
            if boundary == "after_dispatch":
                after_commit(stop)
            return runs

        def saved(repository, run):
            save(repository, run)
            if boundary == "after_finish" and run.status == RunStatus.COMPLETED:
                after_commit(stop)

        async def held_worker(runtime):
            await asyncio.Event().wait()

        with patch.object(manager, "_admit", admitted), patch.object(manager, "_dispatch_request", dispatched), \
             patch.object(RunRepository, "create", created), patch.object(RunRepository, "save", saved):
            if boundary == "after_ack":
                manager._worker = held_worker
            request = await session.run.submit("once per admitted Agent", recipients="all")
            if boundary == "after_ack":
                stop()
            await request.wait(timeout=20)
    raise AssertionError("Crash boundary was not reached")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
