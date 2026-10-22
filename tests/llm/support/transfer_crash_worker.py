"""Real process death around the shared transfer/admission transaction."""
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel
from llm.services.infrastructure.transactions import after_commit
from llm.services.runtime.transfers import TransferAdmission, TransferLedger


async def main(root, boundary):
    root.mkdir(parents=True)
    evidence = {}
    def stop():
        evidence["reached"] = boundary
        with (root / "case.json").open("w") as stream:
            json.dump(evidence, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(93)

    async def action(context):
        with (root / "effects.txt").open("a") as stream:
            stream.write("effect\n")
            stream.flush()
            os.fsync(stream.fileno())
        if boundary == "after_effect":
            stop()
        yield "done"

    async with LargeLanguageModel(root / "workspace", engines={"work": BaseEngine(action=action)}) as app:
        project = await app.projects.acreate()
        await project.agents.acreate({"engine": "work"}, identifier="a")
        source = await project.sessions.acreate()
        target = await project.sessions.acreate(agent_ids=["a"])
        evidence.update(project_id=project.id, source_id=source.id, target_id=target.id)
        write, persist = TransferLedger.write, TransferAdmission.persist
        def written(session, value):
            write(session, value)
            if boundary == "source_receipt" and value["outgoing"]:
                stop()
            if boundary == "target_receipt" and value["incoming"]:
                stop()
        def admitted(admission, message):
            evidence["message_id"] = message.id
            if boundary == "target_input":
                stop()
            result = persist(admission, message)
            if boundary == "after_ack":
                after_commit(stop)
            return result
        with patch.object(TransferLedger, "write", side_effect=written), patch.object(TransferAdmission, "persist", admitted):
            handle = await source.exchange.send(target.id, "once", recipients=["a"], key="same")
            await handle.wait(timeout=15)
    raise AssertionError("Crash boundary was not reached")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
