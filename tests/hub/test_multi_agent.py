"""Hub observes and edits complete recipient groups and keeps review targets distinct."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from llm.components.workflows import WorkflowGraph
from llm.engines.base import BaseEngine
from llm.engines.graph import GraphEngine


class HubMultiAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.effects = self.root / "effects.txt"

    def effect(self, value):
        with self.effects.open("a") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    async def setup_runtime(self, action, **factories):
        self.runtime = HubRuntime(HubConfig(self.root / "workspace", auto_title=False,
            agent_id="a", agent_definition={"engine": "host"},
            engine_factories={"host": lambda: BaseEngine(action=action), **factories}))
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()
        await self.runtime.project.agents.acreate({"engine": "host"}, identifier="b")

    async def test_parallel_snapshot_and_submission_state_keep_each_agent_identity(self):
        entered = {name: asyncio.Event() for name in ("a", "b")}
        release = asyncio.Event()
        async def action(context):
            entered[context.run.agent_id].set()
            await release.wait()
            self.effect(context.run.agent_id)
            yield context.run.agent_id
        await self.setup_runtime(action)
        sid = await self.runtime.new_session()
        session = self.runtime.sessions[sid]
        self.assertEqual(session.data.agent_ids, ["a", "b"])
        request = await session.run.submit("shared", recipients="all")
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 10)
        a = (await self.runtime.submission_state(sid, "a"))["run_id"]
        b = (await self.runtime.submission_state(sid, "b"))["run_id"]
        self.assertTrue(a and b)
        self.assertNotEqual(a, b)
        snapshot = await self.runtime.snapshot()
        self.assertEqual({message.author for message in snapshot.messages if message.role == "assistant"}, {"a", "b"})
        self.assertTrue(await session.run.interrupt(a))
        self.assertEqual((await self.runtime.submission_state(sid, "b"))["run_id"], b)
        release.set()
        runs = await request.wait(timeout=10)
        self.assertEqual([run.data.status for run in runs], ["interrupted", "completed"])
        row, = await self.runtime.turns(sid)
        self.assertNotIn("run_id", row)
        self.assertEqual([item["agent_id"] for item in row["runs"]], ["a", "b"])
        self.assertEqual([item["run_id"] for item in row["runs"]], [a, b])
        self.assertEqual(self.effects.read_text().splitlines(), ["b"])

    async def test_edit_preserves_all_recipients_and_original_run_evidence(self):
        async def action(context):
            text = context.messages[-1].content
            self.effect(context.run.agent_id + ":" + text)
            if text == "original":
                raise RuntimeError("review original failure")
            yield text
        await self.setup_runtime(action)
        sid = await self.runtime.new_session()
        session = self.runtime.sessions[sid]
        request = await session.run.submit("original", recipients="all")
        original = await request.wait(timeout=10)
        self.assertTrue(all(run.data.status == "failed" for run in original))
        before = [(run.data.paths.root / "run.json").read_bytes() for run in original]
        row, = await self.runtime.turns(sid)
        edited = await self.runtime.edit_turn(sid, row["id"], "corrected", row["request_id"])
        following = await session.run.request(edited).wait(timeout=10)
        self.assertEqual([run.data.agent_id for run in following], ["a", "b"])
        self.assertTrue(all(run.data.status == "completed" for run in following))
        self.assertEqual([(run.data.paths.root / "run.json").read_bytes() for run in original], before)
        current, = await self.runtime.turns(sid)
        self.assertEqual(current["id"], row["id"])
        self.assertEqual([item["run_id"] for item in current["runs"]], [run.id for run in following])
        self.assertCountEqual(self.effects.read_text().splitlines(),
                              ["a:original", "b:original", "a:corrected", "b:corrected"])

    async def test_snapshot_keeps_both_pending_reviews_and_resume_changes_only_selected_agent(self):
        async def unused(context):
            yield "unused"
        async def work(node):
            self.effect(node.context.run.agent_id)
            return {}
        await self.setup_runtime(unused, graph=lambda: GraphEngine(handlers={"work": work}))
        flow = (WorkflowGraph(entry="work").node("work", "work", pause_before=True)
                .node("end", "end").connect("work", "end"))
        await self.runtime.project.components.workflows.acreate(flow.to_dict(), identifier="review")
        for name in ("review-a", "review-b"):
            await self.runtime.project.agents.acreate({"engine": "graph", "engine_options": {"workflow": "review"}}, identifier=name)
        sid = await self.runtime.new_session()
        session = self.runtime.sessions[sid]
        request = await session.run.submit("review both", recipients=["review-a", "review-b"])
        runs = await request.wait(timeout=15)
        self.assertTrue(all(run.data.status == "paused" for run in runs), [run.data.error for run in runs])
        self.assertEqual({question["run_id"] for question in (await self.runtime.snapshot()).questions}, {run.id for run in runs})
        self.assertFalse(self.effects.exists())
        approval, = await runs[0].ainteractions(pending_only=True)
        await runs[0].arespond(approval.respond(approval.options[0].id))
        resumed, = await (await session.run.resume(runs[0].id, agent_id="review-a")).wait(timeout=15)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        snapshot = await self.runtime.snapshot()
        self.assertEqual([question["run_id"] for question in snapshot.questions], [runs[1].id])
        self.assertEqual(self.effects.read_text().splitlines(), ["review-a"])
        row, = await self.runtime.turns(sid)
        self.assertEqual([(item["agent_id"], item["run_id"]) for item in row["runs"]],
                         [("review-a", resumed.id), ("review-b", runs[1].id)])
