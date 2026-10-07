"""장기 기억 CRUD와 Loop/Graph Tool을 파일 저장 및 실행 서비스로 통합 검증한다."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components.memory import MemoryComponent, MemoryConflictError
from llm.components.memory.tools import memory_tools
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.agents import AgentComponent
from llm.core.models import RunStatus
from llm.engines.loop import LoopEngine
from llm.engines.base import BaseEngine
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from llm.engines.graph.tool import ToolNode
from llm.llm import LargeLanguageModel
from llm.services.composition import BackendServices
from llm.services.runtime.tools import ToolRuntime, current_tool_call
from tests.llm.test_loop import ScriptedCompletion, call, chunk


def completion_for(name, arguments):
    return ScriptedCompletion(
        [chunk(calls=[call(json.dumps(arguments), name=name)]), chunk(finish="tool_calls")],
        [chunk("기억을 처리했습니다."), chunk(finish="stop")])


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.component = MemoryComponent()
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("Memory", components=["memory"], config={"parameters": {"components": {"memory": {'policy': {'tool_write_status': 'candidate'}, 'config': {'search_strategy': 'keyword', 'search_status': 'confirmed', 'cache_records': 256}}}}})
        self.memory = await self.project.components.aget("memory")

    async def run_tool(self, name, arguments, *, project=None):
        model = completion_for(name, arguments)
        engine = "test_" + str(len(self.app.engines.names()))
        self.app.engines.register(engine, LoopEngine(completion_fn=model).for_agent({"engine": 'loop', "engine_options": LoopEngine.parameter_layout.pack({'completion': {"model": "test/model"}})}))
        session = await (project or self.project).sessions.acreate()
        run = await (await session.run.submit("기억을 관리해줘", engine=engine)).wait(timeout=20)
        return run, model, session

    async def test_crud_open_json_revision_and_history(self):
        identifier = await self.memory.acreate({"content": "설명은 한국어로", "metadata": {"future": {"x": [1]}}}, identifier="language")
        record = await self.memory.aload(identifier)
        self.assertEqual((record["revision"], record["status"], record["scope"]), (1, "confirmed", "project"))
        updated = await self.memory.aupdate(identifier, {"tags": ["응답"]}, expected_revision=1)
        self.assertEqual(updated["metadata"]["future"], {"x": [1]})
        with self.assertRaises(MemoryConflictError):
            await self.memory.aupdate(identifier, {"content": "stale"}, expected_revision=1)
        self.assertEqual(await self.memory.aload(identifier), updated)
        history = await self.memory.ahistory(identifier)
        self.assertEqual([h["operation"] for h in history], ["create", "update"])
        self.assertEqual(history[-1]["data"], updated)
        self.assertEqual(history[0]["source"]["project_id"], self.project.id)
        history[0]["data"]["content"] = "mutated"
        self.assertEqual((await self.memory.ahistory(identifier))[0]["data"]["content"], "설명은 한국어로")
        saved = json.loads((self.project.paths.root / "memory/records/language.json").read_text())
        self.assertEqual(saved["record"], saved["history"][-1]["data"])

    async def test_soft_delete_restore_and_explicit_purge(self):
        identifier = await self.memory.acreate({"content": "remember"})
        with self.assertRaisesRegex(ValueError, "Soft-delete"):
            await self.memory.apurge(identifier, expected_revision=1)
        deleted = await self.memory.adelete(identifier, expected_revision=1)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(await self.memory.alist(), {})
        self.assertEqual(await self.memory.asearch("remember"), [])
        with self.assertRaises(FileNotFoundError):
            await self.memory.aload(identifier)
        self.assertEqual((await self.memory.aload(identifier, include_deleted=True))["revision"], 2)
        with self.assertRaises(ValueError):
            await self.memory.aupdate(identifier, {"content": "bypass"}, expected_revision=2)
        await self.memory.arestore(identifier, expected_revision=2)
        self.assertEqual((await self.memory.aload(identifier))["revision"], 3)
        await self.memory.adelete(identifier, expected_revision=3)
        with self.assertRaises(MemoryConflictError):
            await self.memory.apurge(identifier, expected_revision=3)
        await self.memory.apurge(identifier, expected_revision=4)
        with self.assertRaises(FileNotFoundError):
            await self.memory.ahistory(identifier)

    async def test_search_status_limits_and_unicode(self):
        await self.memory.acreate({"content": "Python 한국어", "tags": ["설명"]}, identifier="confirmed")
        await self.memory.acreate({"content": "Python", "status": "candidate"}, identifier="candidate")
        self.assertEqual(len(await self.memory.asearch("PYTHON")), 1)
        self.assertEqual(len(await self.memory.asearch("python", status="all")), 2)
        self.assertEqual((await self.memory.asearch("한국어"))[0]["memory"]["id"], "confirmed")
        await self.memory.aconfigure({'config': {'search_strategy': 'keyword', 'search_status': 'candidate', 'search_limit': 1}, 'policy': {'max_search_results': 2}})
        self.assertEqual((await self.memory.asearch("python"))[0]["memory"]["id"], "candidate")
        for args in ({"limit": 3}, {"limit": True}, {"status": "unknown"}):
            with self.assertRaises(ValueError):
                await self.memory.asearch("python", **args)
        with self.assertRaises(ValueError):
            await self.memory.aconfigure({'policy': {'tool_write_status': 'automatic'}})

    async def test_validation_and_failed_write_leave_old_revision(self):
        await self.memory.acreate({"content": "valid"}, identifier="m")
        for changes in ({"revision": 9}, {"source": {}}, {"content": ""}, {"scope": "global"}, {"tags": "tag"}):
            with self.assertRaises(ValueError):
                await self.memory.aupdate("m", changes, expected_revision=1)
        with self.assertRaises(ValueError):
            await self.memory.acreate({"content": "escape"}, identifier="../outside")
        with patch("llm.components.base.atomic_json", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                await self.memory.aupdate("m", {"content": "new"}, expected_revision=1)
        self.assertEqual((await self.memory.aload("m"))["revision"], 1)
        self.assertEqual(len(await self.memory.ahistory("m")), 1)

    async def test_full_save_cannot_bypass_revision(self):
        await self.memory.acreate({"content": "before", "metadata": {"extension": True}}, identifier="m")
        data = {"content": "after", "kind": "note", "scope": "project", "status": "confirmed", "tags": []}
        result = await self.memory.asave("m", data, expected_revision=1)
        self.assertEqual(result["revision"], 2)
        self.assertNotIn("extension", result.get("metadata", {}))
        with self.assertRaises(TypeError):
            await self.memory.asave("m", data)

    async def test_concurrent_updates_only_one_revision_wins(self):
        await self.memory.acreate({"content": "original"}, identifier="m")
        results = await asyncio.gather(*(
            self.memory.aupdate("m", {"content": str(index)}, expected_revision=1)
            for index in range(2)), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, MemoryConflictError) for value in results), 1)
        self.assertEqual((await self.memory.aload("m"))["revision"], 2)

    async def test_clone_preserves_history_and_deleted_records(self):
        await self.memory.acreate({"content": "original"}, identifier="m")
        await self.memory.adelete("m", expected_revision=1)
        clone = await self.project.aclone()
        memory = await clone.components.aget("memory")
        self.assertEqual(await memory.ahistory("m"), await self.memory.ahistory("m"))
        self.assertEqual(await memory.alist(), {})
        await memory.arestore("m", expected_revision=2)
        self.assertEqual((await memory.aload("m"))["revision"], 3)
        self.assertTrue((await self.memory.aload("m", include_deleted=True))["deleted"])

    async def test_backup_restore_and_corrupt_deleted_history_validation(self):
        await self.memory.acreate({"content": "archived"}, identifier="m")
        await self.memory.adelete("m", expected_revision=1)
        backup = await self.project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "restored") as app:
            project = await app.projects.arestore_backup(backup)
            memory = await project.components.aget("memory")
            self.assertEqual(await memory.ahistory("m"), await self.memory.ahistory("m"))
        path = self.project.paths.root / "memory/records/m.json"
        content = json.loads(path.read_text())
        content["history"].pop()
        path.write_text(json.dumps(content))
        with self.assertRaisesRegex(ValueError, "history"):
            await self.project.abackup(self.root / "corrupt")

    async def test_handles_check_selection_project_and_backend_lifetime(self):
        await self.memory.acreate({"content": "saved"}, identifier="m")
        await self.project.components.aselect([])
        with self.assertRaises(ValueError):
            await self.memory.alist()
        await self.project.components.aselect(["memory"])
        self.assertEqual(len(await self.memory.alist()), 1)
        await self.project.adelete()
        with self.assertRaises(ValueError):
            await self.memory.alist()
        await self.project.arestore()
        await self.app.shutdown()
        with self.assertRaises(RuntimeError):
            await self.memory.alist()

    async def test_durable_memory_independent_of_volatile_conversation(self):
        await self.project.asave(conversation_storage="memory")
        await self.memory.acreate({"content": "persistent"}, identifier="m")
        await self.app.shutdown()
        async with LargeLanguageModel(self.root / "workspace") as app:
            project = await app.projects.aload(self.project.id)
            self.assertEqual((await project.aget_data()).conversation_storage, "memory")
            self.assertEqual((await (await project.components.aget("memory")).aload("m"))["content"], "persistent")

    async def test_custom_engine_requests_memory_without_tools(self):
        class Recall(BaseEngine):
            required_capabilities = ("memory",)

            async def run(self, context):
                if "tools" in context.capabilities:
                    raise AssertionError("Unrequested Tool capability")
                memory, = context.capabilities["memory"]
                hits = await memory.asearch("한국어")
                yield hits[0]["memory"]["content"]

        await self.memory.acreate({"content": "한국어로 응답"})
        self.app.engines.register("recall", Recall())
        session = await self.project.sessions.acreate()
        run = await (await session.run.submit("recall", engine="recall")).wait(timeout=20)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await run.aresponse()).content, "한국어로 응답")

    async def test_automatic_tools_source_transcript_and_persisted_step(self):
        run, model, session = await self.run_tool("memory_create", {"content": "한국어 사용", "kind": "preference"})
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual({t["function"]["name"] for t in model.requests[0]["tools"]},
                         {"memory_search", "memory_get", "memory_create", "memory_update", "memory_delete", "memory_tool_result"})
        record = json.loads(model.requests[1]["messages"][-1]["content"])
        step = next(s for s in await run.steps.alist() if s.kind == "tool")
        self.assertEqual(record, step.output.data)
        self.assertEqual(record["status"], "candidate")
        self.assertEqual(record["source"], {"kind": "tool", "project_id": self.project.id, "session_id": session.id,
                                          "run_id": run.id, "message_id": run.data.input_message_id, "step_id": step.id})
        self.assertEqual((await self.memory.aload(record["id"])), record)
        self.assertEqual(await self.memory.asearch("한국어"), [])
        await self.memory.aupdate(record["id"], {"status": "confirmed"}, expected_revision=1)
        self.assertEqual(len(await self.memory.asearch("한국어")), 1)
        self.assertIsNone(current_tool_call())
        saved = json.loads((step.paths.root / "step.json").read_text())
        self.assertEqual(saved["metadata"]["output"]["data"], record)

    async def test_model_search_get_update_delete_and_stale_write(self):
        await self.memory.acreate({"content": "Python preference"}, identifier="m")
        for name, args in (("memory_search", {"query": "python"}), ("memory_get", {"identifier": "m"}),
                           ("memory_update", {"identifier": "m", "expected_revision": 1, "changes": {"content": "new"}})):
            run, model, _ = await self.run_tool(name, args)
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
            self.assertIn("tool", [m["role"] for m in model.requests[-1]["messages"]])
        self.assertEqual((await self.memory.aload("m"))["status"], "candidate")
        run, _, _ = await self.run_tool("memory_update", {"identifier": "m", "expected_revision": 1,
                                                         "changes": {"content": "stale"}})
        self.assertEqual(run.data.status, RunStatus.FAILED)
        step = next(s for s in await run.steps.alist() if s.kind == "tool")
        self.assertEqual(step.metadata["error_code"], "memory_conflict")
        run, _, _ = await self.run_tool("memory_delete", {"identifier": "m", "expected_revision": 2})
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(await self.memory.alist(), {})
        self.assertEqual([h["source"]["kind"] for h in await self.memory.ahistory("m")], ["api", "tool", "tool"])

    async def test_model_schema_cannot_forge_provenance_or_confirm(self):
        tools = memory_tools(self.memory)
        for args in ({"content": "x", "status": "confirmed"}, {"content": "x", "source": {"run_id": "fake"}},
                     {"content": "x", "path": "/elsewhere"}):
            with self.assertRaises(ValueError):
                tools.prepare("memory_create", json.dumps(args))
        with self.assertRaises(ValueError):
            await tools.get("memory_create").handler({"content": "no context"})
        self.assertEqual(await self.memory.alist(), {})

    async def test_policy_denial_prevents_memory_effects(self):
        async with LargeLanguageModel(self.root / "denied", components=[MemoryComponent()], engines={},
                                      services=BackendServices()) as app:
            model = completion_for("memory_create", {"content": "denied"})
            app.engines.register("loop", LoopEngine(completion_fn=model).for_agent({"engine": 'loop', "engine_options": LoopEngine.parameter_layout.pack({'completion': {"model": "test/model"}})}))
            project = await app.projects.acreate(components=["memory"], config={"policies": {"tools": {"allowed_tools": ["memory_search"]}}})
            session = await project.sessions.acreate()
            run = await (await session.run.submit("save", engine="loop")).wait(timeout=20)
            self.assertEqual(run.data.status, RunStatus.FAILED)
            self.assertEqual(await (await project.components.aget("memory")).alist(), {})
            step = next(s for s in await run.steps.alist() if s.kind == "tool")
            self.assertEqual(step.metadata["error_code"], "tool_denied")

    async def test_tool_write_status_configuration(self):
        await self.memory.aconfigure({'config': {'search_strategy': 'keyword'}, 'policy': {'tool_write_status': 'confirmed'}})
        run, _, _ = await self.run_tool("memory_create", {"content": "configured"})
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(len(await self.memory.asearch("configured")), 1)

    async def test_concurrent_run_provenance_is_isolated(self):
        other = await self.app.projects.acreate("Other", components=["memory"], config={"parameters": {"components": {"memory": {'policy': {'tool_write_status': 'candidate'}}}}})
        results = await asyncio.gather(self.run_tool("memory_create", {"content": "one"}),
                                       self.run_tool("memory_create", {"content": "two"}, project=other))
        for (run, _, session), project in zip(results, (self.project, other)):
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
            records = await (await project.components.aget("memory")).alist()
            self.assertEqual(len(records), 1)
            source = next(iter(records.values()))["source"]
            self.assertEqual((source["project_id"], source["session_id"], source["run_id"]), (project.id, session.id, run.id))
        self.assertIsNone(current_tool_call())

    async def test_graph_tool_node_and_agent_use_same_memory_tools(self):
        self.app.project_manager.components.register(WorkflowComponent())
        self.app.project_manager.components.register(AgentComponent())
        await self.project.components.aselect(["memory", "workflows", "agents"])
        workflows = await self.project.components.aget("workflows")
        graph = (WorkflowGraph(entry="remember", initial_state={"args": {"content": "graph memory"}})
                 .node("remember", "tool", tool="memory_create", arguments_key="args", result_key="saved")
                 .node("end", "end").connect("remember", "end").to_dict())
        await workflows.acreate(graph, identifier="flow")
        self.app.engines.register("graph", GraphEngine(handlers={"tool": ToolNode()}))
        session = await self.project.sessions.acreate()
        run = await (await session.run.submit("remember", engine="graph", engine_options={"workflow": "flow"})).wait(timeout=20)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        record = next(iter((await self.memory.alist()).values()))
        self.assertEqual(record["source"]["run_id"], run.id)
        model = completion_for("memory_get", {"identifier": record["id"]})
        agents = await self.project.components.aget("agents")
        await agents.acreate({"engine": "loop", "purpose": "Recall", "completion": {"model": "test/model"},
                              "tools": ["memory_get"]}, identifier="reader")
        graph = (WorkflowGraph(entry="reader").node("reader", "agent", agent="reader")
                 .node("end", "end").connect("reader", "end").to_dict())
        await workflows.acreate(graph, identifier="agent_flow")
        self.app.engines.register("agent", GraphEngine(handlers={
            "agent": AgentNode(engines={"loop": LoopEngine(completion_fn=model)})}))
        run = await (await session.run.submit("recall", engine="agent", engine_options={"workflow": "agent_flow"})).wait(timeout=20)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual([t["function"]["name"] for t in model.requests[0]["tools"]], ["memory_get"])
        self.assertEqual(json.loads(model.requests[-1]["messages"][-1]["content"]), record)


if __name__ == "__main__":
    unittest.main()
