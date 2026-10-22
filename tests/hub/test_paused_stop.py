"""Stopping a checkpoint cancels interaction without executing or erasing it."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import tempfile
import unittest
from pathlib import Path

from hub.backend.runtime import HubConfig, HubRuntime
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class PausedStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_boundary_stop_keeps_checkpoint_for_explicit_retry(self):
        from tests.hub.test_execution import config
        from llm.components.workflows import WorkflowGraph
        calls = []
        async def work(context):
            calls.append("work")
            return {}
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(config(root, {"work": work}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                workflows = await runtime.project.components.aget("workflows")
                await workflows.acreate(WorkflowGraph(entry="work").node("work", "work", pause_before=True)
                    .node("end", "end").connect("work", "end").to_dict(), identifier="pause")
                await runtime.submit(sid, "Do work", "graph", {"workflow": "pause"})
                await runtime.sessions[sid].run.wait_idle()
                self.assertEqual((await runtime.snapshot()).run.status, "paused")
                self.assertTrue(await runtime.interrupt(sid))
                self.assertEqual(calls, [])
                row, = await runtime.turns(sid)
                self.assertEqual(row["status"], "interrupted")
                await runtime.retry_turn(sid, row["id"], row["run_id"])
                retried = await runtime.snapshot()
                question = retried.questions[0]
                self.assertEqual(calls, [])
                await runtime.answer_question(retried.project_id, sid, question["run_id"], question["request"],
                                              question["request"]["options"][0]["id"], None)
                await runtime.sessions[sid].run.wait_idle()
                self.assertEqual(calls, ["work"])
                self.assertEqual((await runtime.snapshot()).run.status, "completed")
                self.assertEqual(len(await runtime.turns(sid)), 1)
            finally:
                await runtime.close()

    async def test_stop_reopen_context_and_explicit_retry_requires_approval_again(self):
        provider = ScriptedCompletion([
            chunk("Before approval"),
            chunk(calls=[call('{"path":"result.txt","content":"approved"}', name="file_create")], finish="tool_calls")],
            [chunk("Finished", finish="stop")], [chunk("Next answer", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(Path(root) / "hub", file_root=root, auto_title=False, model="test/model",
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                sid = await runtime.new_session(title="Paused")
                await runtime.submit(sid, "Create the file")
                await runtime.sessions[sid].run.wait_idle()
                before = await runtime.snapshot()
                self.assertEqual(before.run.status, "paused")
                self.assertTrue(before.questions)
                self.assertTrue(await runtime.interrupt(sid))
                stopped = await runtime.snapshot()
                self.assertEqual(stopped.run.status, "interrupted")
                self.assertEqual(stopped.status, "interrupted")
                self.assertFalse(stopped.questions)
                self.assertEqual(stopped.messages[-1].status, "interrupted")
                self.assertEqual(stopped.notifications[-1].status, "interrupted")
                self.assertEqual((await runtime.turns(sid))[0]["status"], "interrupted")
                self.assertFalse(Path(root, "result.txt").exists())
                self.assertEqual(len(provider.requests), 1)
                handle = await runtime.sessions[sid].run.aload(before.run.id)
                self.assertEqual(str((await handle.aget_data()).status), "paused")
                self.assertEqual((await handle.ainteraction_views())[0].status, "cancelled")
                self.assertFalse(await runtime.interrupt(sid))
                await runtime.close()
                runtime = HubRuntime(config)
                await runtime.start()
                stopped = await runtime.snapshot()
                self.assertEqual(stopped.run.status, "interrupted")
                self.assertFalse(stopped.questions)
                row, = await runtime.turns(sid)
                await runtime.retry_turn(sid, row["id"], row["run_id"])
                retried = await runtime.snapshot()
                self.assertEqual(retried.run.status, "paused")
                self.assertTrue(retried.questions)
                self.assertFalse(Path(root, "result.txt").exists())
                question = retried.questions[0]
                await runtime.answer_question(retried.project_id, sid, question["run_id"], question["request"], "approve", None)
                await runtime.sessions[sid].run.wait_idle()
                self.assertEqual(Path(root, "result.txt").read_text(), "approved")
                self.assertEqual((await runtime.snapshot()).run.status, "completed")
                await runtime.submit(sid, "Next request")
                await runtime.sessions[sid].run.wait_idle()
                self.assertIn("Finished", [m.get("content") for m in provider.requests[-1]["messages"]])
            finally:
                await runtime.close()

    async def test_new_request_excludes_stopped_pair(self):
        provider = ScriptedCompletion([chunk("Partial"), chunk(calls=[call(
            '{"path":"never.txt","content":"no"}', name="file_create")], finish="tool_calls")],
            [chunk("New answer", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(Path(root) / "hub", file_root=root, auto_title=False, model="test/model",
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                await runtime.submit(sid, "Stopped request")
                await runtime.sessions[sid].run.wait_idle()
                await runtime.interrupt(sid)
                await runtime.submit(sid, "New request")
                await runtime.sessions[sid].run.wait_idle()
                contents = [m.get("content") for m in provider.requests[-1]["messages"]]
                self.assertNotIn("Stopped request", contents)
                self.assertNotIn("Partial", contents)
                self.assertIn("New request", contents)
                self.assertFalse(Path(root, "never.txt").exists())
                snapshot = await runtime.snapshot()
                self.assertEqual([m.status for m in snapshot.messages if m.role == "assistant"], ["interrupted", "completed"])
            finally:
                await runtime.close()
