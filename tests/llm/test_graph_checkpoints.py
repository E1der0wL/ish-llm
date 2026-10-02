"""노드 경계 저장, 명시적 재개, 부작용 중복 방지와 UI 저장 계약의 통합 검사."""

import asyncio
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from llm.components.workflows import WorkflowGraph
from llm.core.models import MessageStatus, RunStatus
from llm.engines.base import BaseEngine, EngineEventType
from llm.engines.graph import GraphEngine
from llm.llm import LargeLanguageModel
from llm.services.runtime.runs import RunErrorCode, RunRequestError
from tests.llm.test_graph_engine import parallel, straight


class GraphCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_receipts_and_queued_request_roll_back_together(self):
        from llm.services.history.conversation import ConversationStore
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {"value": node.node_id}
        graph = (WorkflowGraph(entry="first").node("first", "work")
                 .node("second", "work", pause_before=True).node("end", "end")
                 .connect("first", "second").connect("second", "end").to_dict())
        await self.setup_graph(graph, work)
        paused = await self.run_graph()
        snapshot = await paused.acheckpoint()
        original = ConversationStore.create
        def fail_after_create(store, *args, **kwargs):
            value = original(store, *args, **kwargs)
            if "resume" in (kwargs.get("metadata") or {}):
                raise OSError("queue confirmation failed")
            return value
        with patch.object(ConversationStore, "create", fail_after_create):
            with self.assertRaises(OSError):
                await self.session.run.resume(paused.id, engine="graph", decisions={'["second"]': {}})
        self.assertEqual(await paused.acheckpoint(), snapshot)
        self.assertEqual(len(await self.session.aconversation()), 2)
        self.assertEqual(self.app.run_repository.interaction_responses(self.session.data, paused.data), [])
        self.assertEqual(calls, ["first"])
        resumed = await self.resume(paused, decisions={'["second"]': {}})
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(len(await self.session.run.alist()), 2)

    async def test_reviewed_state_patch_at_waiting_node(self):
        async def work(node):
            return {'value': node.state.get('value', 'draft')}
        graph = (WorkflowGraph(entry='first').node('first', 'work')
            .node('review', 'work', pause_before=True, resume_schema={
                'type': 'object', 'properties': {'value': {'type': 'string'}}, 'additionalProperties': False})
            .node('end', 'end').connect('first', 'review').connect('review', 'end').to_dict())
        await self.setup_graph(graph, work)
        paused = await self.run_graph()
        with self.assertRaises(Exception):
            await self.session.run.resume(paused.id, engine='graph', decisions={'["review"]': {'state': {'other': 1}}})
        resumed = await self.resume(paused, decisions={'["review"]': {'state': {'value': 'reviewed'}}})
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual((await self.output(resumed))['value'], 'reviewed')

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def setup_graph(self, graph, work, **options):
        self.events = []
        self.engine = GraphEngine("flow", handlers={"work": work}, **options)
        self.app = LargeLanguageModel(self.root, engines={"graph": self.engine},
                                     on_run_event=lambda event: self.events.append(event))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("test", components=["workflows"])
        self.workflows = await self.project.components.aget("workflows")
        await self.workflows.acreate(graph, identifier="flow")
        self.session = await self.project.sessions.acreate()

    async def run_graph(self):
        return await (await self.session.run.submit("original request", engine="graph")).wait(timeout=30)

    async def resume(self, run, **options):
        return await (await self.session.run.resume(run.id, engine="graph", **options)).wait(timeout=30)

    async def output(self, run):
        return next(s.output.data for s in await run.steps.alist() if s.kind == "graph")

    async def uncertain(self, run):
        return [key for key, value in (await run.acheckpoint())["records"].items()
                if value["status"] == "started" and value["node_type"] == "work"]

    async def test_pause_resume_reopen_keeps_completed_side_effects_and_original_run(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {"seen": node.state.get("seen", []) + [node.node_id]}
        graph = (WorkflowGraph(entry="first").node("first", "work")
                 .node("second", "work", pause_before=True).node("end", "end")
                 .connect("first", "second").connect("second", "end").to_dict())
        await self.setup_graph(graph, work)
        paused = await self.run_graph()
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        self.assertEqual(paused.response.status, MessageStatus.PAUSED)
        self.assertEqual(self.events[-1].type.value, "paused")
        snapshot = await paused.acheckpoint()
        self.assertEqual(snapshot["records"]['["first"]']["status"], "completed")
        self.assertEqual(snapshot["records"]['["second"]']["status"], "waiting")
        self.assertEqual(calls, ["first"])
        project_id, session_id, old_id = self.project.id, self.session.id, paused.id
        await self.app.shutdown()
        app = LargeLanguageModel(self.root, engines={"graph": self.engine})
        self.addAsyncCleanup(app.shutdown)
        session = (await app.projects.aload(project_id)).sessions.load(session_id)
        resumed = await (await session.run.resume(old_id, engine="graph")).wait(timeout=30)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual((await self.output(resumed))["seen"], ["first", "second"])
        self.assertEqual(session.run.load(old_id).data.status, RunStatus.PAUSED)
        self.assertEqual(resumed.data.metadata["resume"]["run_id"], old_id)
        self.assertTrue(any(s.metadata.get("reused") for s in await resumed.steps.alist()))

    async def test_interrupted_node_requires_explicit_retry_and_duplicate_resume_is_rejected(self):
        entered = asyncio.Event()
        calls = []
        async def work(node):
            calls.append(node.node_id)
            if len(calls) == 1:
                entered.set()
                await asyncio.Event().wait()
            return {"done": True}
        await self.setup_graph(straight(), work)
        request = await self.session.run.submit("run", engine="graph")
        await asyncio.wait_for(entered.wait(), 10)
        await self.session.run.interrupt()
        stopped = await request.wait()
        self.assertEqual(stopped.data.status, RunStatus.INTERRUPTED)
        with self.assertRaises(RunRequestError) as error:
            await self.session.run.resume(stopped.id, engine="graph")
        self.assertEqual(error.exception.code, RunErrorCode.RESUME_REJECTED)
        retried = await self.resume(stopped, retry_nodes=await self.uncertain(stopped))
        self.assertEqual(retried.data.status, RunStatus.COMPLETED, retried.data.error)
        self.assertEqual(calls, ["work", "work"])
        with self.assertRaisesRegex(RunRequestError, "already has"):
            await self.session.run.resume(stopped.id, engine="graph", retry_nodes=await self.uncertain(stopped))
        with self.assertRaises(RunRequestError):
            await self.session.run.resume(retried.id, engine="graph")

    async def test_project_policy_change_requires_original_settings_for_resume(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        self.assertEqual(paused.data.status, RunStatus.PAUSED)
        await self.project.aconfigure_policies({"context": {"mode": "recent", "max_turns": 1}})
        with self.assertRaises(RunRequestError) as caught:
            await self.session.run.resume(paused.id, engine="graph")
        self.assertEqual(caught.exception.code, "resume_rejected")
        self.assertEqual(calls, [])
        await self.project.asave(config={**self.project.data.config.to_dict(), "policies": paused.data.metadata["policies"]})
        resumed = await self.resume(paused)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(calls, ["work"])

    async def test_parallel_completed_branch_not_reexecuted_after_other_branch_fails(self):
        finished = asyncio.Event()
        calls = []
        async def work(node):
            calls.append(node.node_id)
            if node.node_id == "b" and calls.count("b") == 1:
                await finished.wait()
                raise ValueError("branch fails once")
            return {"value": node.node_id}
        await self.setup_graph(parallel(), work)
        def observe(run, event):
            if (event.type == EngineEventType.CHECKPOINT and event.metadata.get("key") == '["fork","branch","a","a"]'
                    and event.metadata.get("value", {}).get("status") == "completed"):
                finished.set()
        self.app._manager(self.session._snapshot).on_event = observe
        failed = await self.run_graph()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        resumed = await self.resume(failed, retry_nodes=await self.uncertain(failed))
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(calls.count("a"), 1)
        self.assertEqual(calls.count("b"), 2)
        self.assertEqual((await self.output(resumed))["branches"]["a"]["value"], "a")

    async def test_pause_in_loop_tracks_each_iteration_separately(self):
        calls = []
        async def work(node):
            calls.append(node.state.get("count", 0))
            return {"count": node.state.get("count", 0) + 1}
        graph = (WorkflowGraph(entry="repeat").node("repeat", "loop", body=straight(pause_before=True),
                 max_iterations=2, on_limit="fail").node("end", "end").connect("repeat", "end").to_dict())
        await self.setup_graph(graph, work)
        first = await self.run_graph()
        second = await self.resume(first)
        self.assertEqual(second.data.status, RunStatus.PAUSED, second.data.error)
        third = await self.resume(second)
        self.assertEqual(third.data.status, RunStatus.COMPLETED, third.data.error)
        self.assertEqual(calls, [0, 1])
        self.assertEqual((await self.output(third))["count"], 2)

    async def test_checkpoint_write_failure_prevents_handler_side_effect(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        await self.setup_graph(straight(), work)
        original = self.app.run_repository.record_checkpoint
        def fail_start(run, event):
            if event.metadata.get("value", {}).get("status") == "started":
                raise OSError("disk unavailable")
            original(run, event)
        with patch.object(self.app.run_repository, "record_checkpoint", side_effect=fail_start):
            failed = await self.run_graph()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertEqual(calls, [])
        resumed = await self.resume(failed)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)

    async def test_completed_handler_without_completion_commit_is_uncertain(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        await self.setup_graph(straight(), work)
        original = self.app.run_repository.record_checkpoint
        def fail_commit(run, event):
            if event.metadata.get("value", {}).get("status") == "completed":
                raise OSError("completion not committed")
            original(run, event)
        with patch.object(self.app.run_repository, "record_checkpoint", side_effect=fail_commit):
            failed = await self.run_graph()
        self.assertEqual(calls, ["work"])
        with self.assertRaisesRegex(RunRequestError, "retry_nodes"):
            await self.session.run.resume(failed.id, engine="graph")

    async def test_revision_and_ownership_validation(self):
        async def work(node):
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        other = await self.project.sessions.acreate()
        with self.assertRaises(RunRequestError):
            await other.run.resume(paused.id, engine="graph")
        self.engine.revision = "2"
        with self.assertRaisesRegex(RunRequestError, "revision"):
            await self.session.run.resume(paused.id, engine="graph")

    async def test_definition_changes_never_mix_with_previous_results(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        changed = straight(pause_before=True)
        changed["initial_state"] = {"new": True}
        await self.workflows.asave("flow", changed)
        with self.assertRaisesRegex(RunRequestError, "changed"):
            await self.resume(paused)
        self.assertEqual(calls, [])

    async def test_ui_definition_is_one_json_document_with_nodes_and_layout(self):
        async def work(node):
            return {}
        graph = straight(pause_before=True, ui={"position": {"x": 100, "y": 200}})
        graph["ui"] = {"zoom": 1.5}
        await self.setup_graph(graph, work)
        path = self.project.paths.root / "workflows" / "records" / "flow.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), graph)
        self.assertEqual(await self.workflows.aload("flow"), graph)
        self.assertFalse((path.parent / "work.json").exists())
        malformed = {**graph, "nodes": {**graph["nodes"], "work": {"type": "work", "pause_before": "yes"}}}
        with self.assertRaisesRegex(ValueError, "pause_before"):
            await self.workflows.asave("flow", malformed)
        self.assertEqual(await self.workflows.aload("flow"), graph)

    async def test_actual_process_crash_never_replays_until_explicit_resume(self):
        process = await asyncio.to_thread(subprocess.run, [sys.executable, "-m",
            "tests.llm.support.graph_checkpoint_worker", str(self.root)], capture_output=True, timeout=45)
        self.assertEqual(process.returncode, 23, process.stderr)
        async def work(node):
            with (self.root / "effects.txt").open("a", encoding="utf-8") as stream:
                stream.write(node.node_id + "\n")
            return {"second_done": True}
        app = LargeLanguageModel(self.root, engines={"graph": GraphEngine("flow", handlers={"work": work})})
        self.addAsyncCleanup(app.shutdown)
        project = (await app.projects.alist())[0]
        session = (await project.sessions.alist())[0]
        await session.run.start()
        await session.run.wait_idle()
        previous = (await session.run.alist())[0]
        self.assertEqual(previous.data.status, RunStatus.INTERRUPTED)
        self.assertEqual((self.root / "effects.txt").read_text().splitlines(), ["first", "second"])
        with self.assertRaises(RunRequestError):
            await session.run.resume(previous.id, engine="graph")
        resumed = await (await session.run.resume(previous.id, engine="graph",
                        retry_nodes=['["second"]'])).wait(timeout=30)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        # 실제 부작용 후 종료된 노드를 승인했으므로 해당 부작용은 중복될 수 있다.
        self.assertEqual((self.root / "effects.txt").read_text().splitlines(), ["first", "second", "second"])

    async def test_durable_queued_resume_survives_shutdown_and_is_not_duplicated(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        entered = asyncio.Event()
        async def wait(context):
            entered.set()
            await asyncio.Event().wait()
        self.app.engines.register("wait", BaseEngine(action=wait))
        await self.session.run.submit("wait", engine="wait")
        await asyncio.wait_for(entered.wait(), 10)
        request = await self.session.run.resume(paused.id, engine="graph")
        self.assertEqual(request.data.status, MessageStatus.QUEUED)
        with self.assertRaisesRegex(RunRequestError, "already has"):
            await self.session.run.resume(paused.id, engine="graph")
        pid, tid, mid = self.project.id, self.session.id, request.id
        await self.app.shutdown()
        app = LargeLanguageModel(self.root, engines={"graph": self.engine})
        self.addAsyncCleanup(app.shutdown)
        session = (await app.projects.aload(pid)).sessions.load(tid)
        resumed = await session.run.request(mid).wait(timeout=30)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(calls, ["work"])

    async def test_memory_mode_does_not_save_transcript_in_checkpoint_or_resume_after_shutdown(self):
        async def work(node):
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        memory = await self.app.projects.acreate("memory", components=["workflows"], conversation_storage="memory")
        await (await memory.components.aget("workflows")).acreate(straight(pause_before=True), identifier="flow")
        session = await memory.sessions.acreate()
        paused = await (await session.run.submit("private memory transcript marker", engine="graph")).wait()
        checkpoint = await paused.acheckpoint()
        self.assertNotIn("private memory transcript marker", json.dumps(checkpoint))
        pid, tid, rid = memory.id, session.id, paused.id
        await self.app.shutdown()
        app = LargeLanguageModel(self.root, engines={"graph": self.engine})
        self.addAsyncCleanup(app.shutdown)
        session = (await app.projects.aload(pid)).sessions.load(tid)
        with self.assertRaisesRegex(RunRequestError, "Original conversation"):
            await session.run.resume(rid, engine="graph")

    async def test_two_concurrent_resumes_admit_only_one_request(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        results = await asyncio.gather(self.session.run.resume(paused.id, engine="graph"),
                                       self.session.run.resume(paused.id, engine="graph"), return_exceptions=True)
        accepted = [result for result in results if not isinstance(result, Exception)]
        self.assertEqual(len(accepted), 1)
        self.assertTrue(any(isinstance(result, RunRequestError) for result in results))
        self.assertEqual((await accepted[0].wait()).data.status, RunStatus.COMPLETED)
        self.assertEqual(calls, ["work"])

    async def test_paused_run_releases_session_queue_and_resume_restores_original_context(self):
        observations = []
        async def work(node):
            observations.append([message.content for message in node.context.messages])
            self.assertEqual(node.context.messages[-1].id, node.context.run.input_message_id)
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        first = await self.session.run.submit("first", engine="graph")
        second = await self.session.run.submit("second", engine="graph")
        first_run, second_run = await first.wait(), await second.wait()
        self.assertEqual(first_run.data.status, RunStatus.PAUSED)
        self.assertEqual(second_run.data.status, RunStatus.PAUSED)
        self.assertEqual((await self.resume(first_run)).data.status, RunStatus.COMPLETED)
        self.assertEqual(observations, [["first"]])

    async def test_parallel_pause_marks_cancelled_inflight_branch_uncertain(self):
        entered = asyncio.Event()
        calls = []
        async def work(node):
            calls.append(node.node_id)
            if node.node_id == "a":
                await entered.wait()
            if node.node_id == "b" and calls.count("b") == 1:
                entered.set()
                await asyncio.Event().wait()
            return {}
        graph = parallel()
        graph["nodes"]["approve"] = {"type": "work", "pause_before": True}
        graph["edges"] = [edge for edge in graph["edges"] if edge["source"] != "a"] + [
            {"source": "a", "target": "approve"}, {"source": "approve", "target": "join"}]
        await self.setup_graph(graph, work)
        paused = await self.run_graph()
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        uncertain = await self.uncertain(paused)
        self.assertEqual(uncertain, ['["fork","branch","b","b"]'])
        with self.assertRaises(RunRequestError):
            await self.session.run.resume(paused.id, engine="graph")
        resumed = await self.resume(paused, retry_nodes=uncertain)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(calls.count("a"), 1)
        self.assertEqual(calls.count("b"), 2)
        self.assertEqual(calls.count("approve"), 1)

    async def test_resume_rejects_snapshot_tampering_while_queued(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(context):
            entered.set()
            await release.wait()
        self.app.engines.register("wait", BaseEngine(action=wait))
        blocker = await self.session.run.submit("block", engine="wait")
        await asyncio.wait_for(entered.wait(), 10)
        queued = await self.session.run.resume(paused.id, engine="graph")
        path = paused.data.paths.state / "checkpoints" / "graph" / "checkpoint.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["header"]["initial_state"]["tampered"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        release.set()
        await blocker.wait()
        failed = await queued.wait()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertIn("changed after admission", failed.data.error)
        self.assertEqual(calls, [])

    async def test_resume_after_failed_preparation_keeps_checkpoint_for_later_attempt(self):
        async def work(node):
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(context):
            entered.set()
            await release.wait()
        self.app.engines.register("wait", BaseEngine(action=wait))
        await self.session.run.submit("block", engine="wait")
        await asyncio.wait_for(entered.wait(), 10)
        queued = await self.session.run.resume(paused.id, engine="graph")
        await self.workflows.asave("flow", straight(pause_before=True, label="changed after queue"))
        release.set()
        rejected = await queued.wait()
        self.assertEqual(rejected.data.status, RunStatus.FAILED)
        self.assertIn("changed", rejected.data.error)
        self.assertTrue((await rejected.acheckpoint())["records"])
        await self.workflows.asave("flow", straight(pause_before=True))
        resumed = await self.resume(rejected)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)

    async def test_failed_initial_copy_can_resume_but_missing_initialized_snapshot_is_corruption(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        with patch.object(self.app.run_repository, "record_checkpoint", side_effect=OSError("copy failed")):
            failed = await self.resume(paused)
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertNotIn("checkpoints", failed.data.metadata)
        self.assertEqual((await failed.acheckpoint())["run_id"], failed.id)
        continued = await self.resume(failed)
        self.assertEqual(continued.data.status, RunStatus.COMPLETED, continued.data.error)
        self.assertEqual(calls, ["work"])
        # 초기화가 ACK된 Run의 체크포인트 누락은 원본으로 조용히 우회하지 않는다.
        path = continued.data.paths.state / "checkpoints" / "graph" / "checkpoint.json"
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            await continued.acheckpoint()

    async def test_invalid_handler_registration_is_rejected_before_admission(self):
        async def work(node):
            return {}
        await self.setup_graph(straight(pause_before=True), work)
        paused = await self.run_graph()
        self.engine.handlers.clear()
        with self.assertRaises(RunRequestError) as error:
            await self.session.run.resume(paused.id, engine="graph")
        self.assertEqual(error.exception.code, RunErrorCode.RESUME_REJECTED)
        self.assertEqual(len(await self.session.run.alist()), 1)
