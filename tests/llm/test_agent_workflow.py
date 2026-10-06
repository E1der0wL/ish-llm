from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.configuration_fixtures import rag_project
"""범용 Agent와 Workflow 바인딩을 실제 저장/Tool/RAG/검증 경로로 통합 검증한다."""

import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import asynccontextmanager

from examples.llm.code_workflow import CodeChecks, coding_workflow, report
from llm.components.agents import AgentComponent
from llm.components.skills import SkillComponent
from llm.components.mcp import MCPComponent
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.tools import Tool, ToolRegistry
from llm.components.tools.component import ToolComponent
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.workflows.bindings import bind
from llm.components.workflows.graph import validate_graph
from llm.core.models import RunStatus, StepStatus
from llm.engines.graph.agent import AgentNode
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.graph.tool import ToolNode
from llm.llm import LargeLanguageModel
from tests.llm.test_loop import ScriptedCompletion, call, chunk
from tests.llm.test_rag_components import Extractor, fake_embedding


def answer(text):
    return [chunk(text), chunk(finish="stop")]


def agent_graph(**options):
    return (WorkflowGraph(entry="agent", inputs={"request": "/prompt"},
                          outputs={"answer": "/answer"})
            .node("agent", "agent", agent="writer", inputs={"request": "/request"},
                  outputs={"answer": "/text"}, **options)
            .node("end", "end").connect("agent", "end").to_dict())


class AgentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []

        async def echo(arguments):
            self.calls.append(arguments)
            return {"value": arguments["value"]}

        self.catalog = ToolRegistry(tuple(Tool(name, name, {
            "type": "object", "properties": {"value": {"type": "string"}},
            "required": ["value"], "additionalProperties": False,
        }, echo) for name in ("echo", "forbidden")))
        self.profile = {"engine": "loop", "purpose": "Write", "system_prompt": "Follow the provided request.",
                        "completion": {"model": "test/writer", "temperature": 0.2},
                        "tools": ["echo"], "engine_options": {'policy': {'max_iterations': 3}}}

    async def setup_graph(self, graph, completion, *, handlers=None, extra_components=()):
        components = [AgentComponent(), WorkflowComponent(), RuntimeTools(self.catalog), *extra_components]
        self.backend = LargeLanguageModel(self.root, components=components, engines={
            "graph": GraphEngine(handlers=handlers or {"agent": AgentNode(engines={"loop": LoopEngine(completion_fn=completion)})})})
        self.addAsyncCleanup(self.backend.shutdown)
        self.project = await self.backend.projects.acreate("Workflow", components=[c.name for c in components], config=rag_project() if any(c.name == "rag" for c in components) else {})
        self.agents = await self.project.components.aget("agents")
        await self.agents.acreate(self.profile, identifier="writer")
        self.workflows = await self.project.components.aget("workflows")
        await self.workflows.acreate(graph, identifier="flow")
        await (await self.project.components.aget("tools")).aenable("echo", "forbidden")
        self.session = await self.project.sessions.acreate("Request")

    async def run_graph(self, prompt="Please write"):
        return await (await self.session.run.submit(prompt, engine="graph", engine_options={"workflow": "flow"})).wait(timeout=40)

    async def output(self, run):
        return next(s for s in await run.steps.alist() if s.kind == "graph").output.data

    async def test_agent_model_prompt_allowlist_tool_transcript_and_result_persist(self):
        model = ScriptedCompletion(
            [chunk("intermediate", calls=[call('{"value":"evidence"}', name="echo")]), chunk(finish="tool_calls")],
            answer("final answer"))
        await self.setup_graph(agent_graph(), model)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        self.assertEqual(await self.output(run), {"answer": "final answer"})
        request = model.requests[0]
        self.assertEqual(request["model"], "test/writer")
        self.assertEqual(request["temperature"], 0.2)
        self.assertEqual(request["messages"][0]["content"], self.profile["system_prompt"])
        self.assertEqual(json.loads(request["messages"][1]["content"]), {"request": "Please write"})
        self.assertEqual([t["function"]["name"] for t in request["tools"]], ["echo"])
        self.assertEqual(json.loads(model.requests[1]["messages"][-1]["content"]), {"value": "evidence"})
        steps = await run.steps.alist()
        self.assertTrue(all(s.status == StepStatus.COMPLETED and s.run_id == run.id for s in steps))
        llm_steps = [s for s in steps if s.kind == "llm"]
        self.assertEqual(len(llm_steps), 2)
        agent_step = next(s for s in steps if s.kind == "agent")
        self.assertEqual(agent_step.metadata["agent"], self.profile)
        self.assertEqual(llm_steps[0].metadata["agent_step_id"], agent_step.id)
        self.assertEqual(len(run.data.metadata["completions"]), 2)
        self.assertEqual((await run.aresponse()).content, "")  # 내부 중간 출력은 대화에 섞이지 않는다.
        identifiers = self.project.id, self.session.id, run.id
        await self.backend.shutdown()
        async with LargeLanguageModel(self.root, engines={}) as reopened:
            project = await reopened.projects.aload(identifiers[0])
            session = await project.sessions.aload(identifiers[1])
            loaded = await session.run.aload(identifiers[2])
            self.assertEqual(await self.output(loaded), {"answer": "final answer"})
            self.assertEqual(len(await session.run.alist()), 1)

    async def test_forbidden_tool_batch_has_no_side_effects(self):
        model = ScriptedCompletion([chunk(calls=[call('{"value":"ok"}', name="echo"),
            call('{"value":"bad"}', name="forbidden", index=1, call_id="call_2")]), chunk(finish="tool_calls")])
        await self.setup_graph(agent_graph(), model)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        self.assertEqual(self.calls, [])

    async def test_agent_iteration_limit_and_queued_followup(self):
        self.profile["engine_options"]['policy']['max_iterations'] = 1
        model = ScriptedCompletion([chunk(calls=[call('{"value":"x"}', name="echo")]), chunk(finish="tool_calls")],
                                   answer("next"))
        await self.setup_graph(agent_graph(), model)
        first = await self.session.run.submit("first", engine="graph", engine_options={"workflow": "flow"})
        second = await self.session.run.submit("second", engine="graph", engine_options={"workflow": "flow"})
        self.assertEqual((await first.wait()).data.status, RunStatus.FAILED)
        completed = await second.wait()
        self.assertEqual(completed.data.status, RunStatus.COMPLETED)
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads(model.requests[1]["messages"][1]["content"]), {"request": "second"})

    async def test_all_agent_policies_preflight_before_earlier_side_effect(self):
        self.profile["engine_options"]['policy']['max_iterations'] = 0
        async def write(node):
            self.calls.append("write")
            return {}
        graph = (WorkflowGraph(entry="write").node("write", "write").node("agent", "agent", agent="writer")
                 .node("end", "end").connect("write", "agent").connect("agent", "end").to_dict())
        model = ScriptedCompletion()
        await self.setup_graph(graph, model, handlers={"write": write, "agent": AgentNode(engines={"loop": LoopEngine(completion_fn=model)})})
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.calls, [])
        self.assertEqual(await run.steps.alist(), [])

    async def test_steerable_agent_contract_preflight_prevents_tool_and_mcp_effects(self):
        from llm.core.steering import SteeringMode
        connected = []

        class Incomplete(BaseEngine):
            steering_mode = SteeringMode.CONSUME
            checkpoint_name = "custom"
            def for_agent(self, definition):
                return self

        @asynccontextmanager
        async def connector(definition):
            connected.append(definition)
            yield self.catalog

        self.profile = {"engine": "custom", "purpose": "Work",
                        "resources": {"mcp": {"docs": {"read_docs": "echo"}}}}
        graph = (WorkflowGraph(entry="effect")
                 .node("effect", "tool", tool="echo", arguments={"value": "must not execute"})
                 .node("agent", "agent", agent="writer").node("end", "end")
                 .connect("effect", "agent").connect("agent", "end").to_dict())
        await self.setup_graph(graph, None, handlers={"tool": ToolNode(),
            "agent": AgentNode(engines={"custom": Incomplete()})},
            extra_components=(MCPComponent(connector=connector),))
        await (await self.project.components.aget("mcp")).acreate(
            {"transport": "streamable_http", "url": "https://example.invalid/mcp"}, identifier="docs")
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertIn("checkpoint_name and validate_resume", run.data.error)
        self.assertEqual(self.calls, [])
        self.assertEqual(connected, [])
        self.assertEqual(await run.steps.alist(), [])

    async def test_schema_failure_prevents_next_handler(self):
        model = ScriptedCompletion(answer('{"code":42}'))
        graph = agent_graph(output_format="json", output_schema={"type": "object", "properties": {
            "data": {"type": "object", "properties": {"code": {"type": "string"}}}}})
        await self.setup_graph(graph, model)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertFalse(any(s.name == "end" for s in await run.steps.alist()))

    async def test_invalid_json_is_execution_failure(self):
        await self.setup_graph(agent_graph(output_format="json"), ScriptedCompletion(answer("not json")))
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)

    async def test_missing_input_and_input_schema_fail_before_call(self):
        model = ScriptedCompletion()
        graph = agent_graph()
        graph["nodes"]["agent"]["inputs"] = {"request": "/missing"}
        await self.setup_graph(graph, model)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        graph["nodes"]["agent"]["inputs"] = {"request": "/request"}
        graph["nodes"]["agent"]["input_schema"] = {"type": "object", "properties": {"request": {"type": "integer"}}}
        await self.workflows.asave("flow", graph)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        self.assertEqual(model.requests, [])

    async def test_workflow_output_schema_and_agent_text_opt_in(self):
        model = ScriptedCompletion(answer("visible"))
        graph = agent_graph(emit_text=True)
        graph["output_schema"] = {"type": "object", "required": ["missing"]}
        await self.setup_graph(graph, model)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual((await run.aresponse()).content, "visible")
        self.assertNotIn("output", next(s for s in await run.steps.alist() if s.kind == "graph").metadata)

    async def test_tool_node_uses_mapped_input_and_output(self):
        graph = (WorkflowGraph(entry="tool", inputs={"request": "/prompt"}, outputs={"answer": "/answer"})
                 .node("tool", "tool", tool="echo", inputs={"value": "/request"}, outputs={"answer": "/tool/value"})
                 .node("end", "end").connect("tool", "end").to_dict())
        await self.setup_graph(graph, None, handlers={"tool": ToolNode()})
        run = await self.run_graph("mapped")
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        self.assertEqual(await self.output(run), {"answer": "mapped"})

    async def test_agent_interrupt_drains_tool_and_preserves_queued_request(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def blocking(arguments):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        self.catalog = ToolRegistry((Tool("echo", "wait", {"type": "object"}, blocking),
                                     self.catalog.get("forbidden")))
        model = ScriptedCompletion([chunk(calls=[call('{}', name="echo")]), chunk(finish="tool_calls")],
                                   answer("next"))
        await self.setup_graph(agent_graph(), model)
        first = await self.session.run.submit("first", engine="graph", engine_options={"workflow": "flow"})
        await asyncio.wait_for(entered.wait(), 10)
        second = await self.session.run.submit("second", engine="graph", engine_options={"workflow": "flow"})
        await self.session.run.interrupt()
        interrupted = await first.wait(timeout=10)
        self.assertEqual(interrupted.data.status, RunStatus.INTERRUPTED)
        self.assertTrue(closed.is_set())
        self.assertTrue(all(s.status not in (StepStatus.RUNNING, StepStatus.PENDING)
                            for s in await interrupted.steps.alist()))
        self.assertEqual((await second.wait(timeout=10)).data.status, RunStatus.COMPLETED)

    async def test_absent_allowlist_exposes_no_tools_and_project_model_is_overridden(self):
        self.profile.pop("tools")
        model = ScriptedCompletion(answer("ok"))
        await self.setup_graph(agent_graph(), model)
        data = await self.project.aget_data()
        data.config.parameters["engines"] = {"loop": {"config": {"completion": {"model": "project/default", "temperature": 0.9}}}}
        await self.project.asave(config=data.config)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        self.assertNotIn("tools", model.requests[0])
        self.assertEqual(model.requests[0]["model"], "test/writer")
        self.assertEqual(model.requests[0]["temperature"], 0.2)

    async def test_workflow_input_schema_failure_and_missing_output(self):
        model = ScriptedCompletion(answer("ok"))
        graph = agent_graph()
        graph["input_schema"] = {"type": "object", "required": ["missing"]}
        await self.setup_graph(graph, model)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        self.assertEqual(model.requests, [])
        del graph["input_schema"]
        graph["outputs"] = {"result": "/absent"}
        await self.workflows.asave("flow", graph)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)

    async def test_parallel_agents_do_not_share_inputs_or_results(self):
        def model(**request):
            inputs = json.loads(request["messages"][-1]["content"])
            yield from answer(inputs["label"])
        graph = (WorkflowGraph(entry="fork", initial_state={"a": "first", "b": "second"})
                 .node("fork", "parallel", join="join")
                 .node("a", "agent", agent="writer", inputs={"label": "/a"}, outputs={"value": "/text"})
                 .node("b", "agent", agent="writer", inputs={"label": "/b"}, outputs={"value": "/text"})
                 .node("join", "join").node("end", "end")
                 .connect("fork", "a").connect("fork", "b").connect("a", "join").connect("b", "join")
                 .connect("join", "end").to_dict())
        await self.setup_graph(graph, model)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        branches = (await self.output(run))["branches"]
        self.assertEqual((branches["a"]["value"], branches["b"]["value"]), ("first", "second"))

    async def test_agent_definition_revision_cas_and_open_extension_keys(self):
        await self.setup_graph(agent_graph(), ScriptedCompletion(answer("ok")))
        snapshot = await self.agents.asnapshot("writer")
        definition = snapshot["definition"]
        definition["metadata"] = {"ui": {"color": "blue"}}
        changed = await self.agents.arevise("writer", definition, expected_revision=snapshot["revision"])
        self.assertNotEqual(snapshot["revision"], changed["revision"])
        with self.assertRaisesRegex(ValueError, "changed"):
            await self.agents.arevise("writer", definition, expected_revision=snapshot["revision"])
        run = await self.run_graph()
        step = next(s for s in await run.steps.alist() if s.kind == "agent")
        self.assertEqual(step.metadata["agent_revision"], changed["revision"])
        self.assertEqual(step.metadata["agent"]["metadata"]["ui"], {"color": "blue"})

    async def test_skill_prompt_and_changed_skill_blocks_checkpoint_resume(self):
        self.profile["resources"] = {"skills": ["review"]}
        model = ScriptedCompletion(answer("ok"))
        await self.setup_graph(agent_graph(pause_before=True), model, extra_components=(SkillComponent(),))
        skills = await self.project.components.aget("skills")
        await skills.acreate({"instructions": "Check invariants."}, identifier="review")
        paused = await self.run_graph()
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        await skills.asave("review", {"instructions": "Changed guidance."})
        with self.assertRaisesRegex(Exception, "changed"):
            await self.session.run.resume(paused.id, engine="graph")
        self.assertEqual(model.requests, [])
        await skills.asave("review", {"instructions": "Check invariants."})
        run = await (await self.session.run.resume(paused.id, engine="graph")).wait()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertIn("Skill review:\nCheck invariants.", model.requests[0]["messages"][0]["content"])

    async def test_agent_io_schema_is_reusable_across_nodes(self):
        self.profile["input_schema"] = {"type": "object", "required": ["required"]}
        model = ScriptedCompletion(answer('{"ok":false}'))
        await self.setup_graph(agent_graph(), model)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        self.assertEqual(model.requests, [])
        self.profile.pop("input_schema")
        self.profile.update(output_format="json", output_schema={"type": "object", "properties": {
            "data": {"type": "object", "properties": {"ok": {"const": True}}}}})
        await self.agents.asave("writer", self.profile)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertTrue(any(s.kind == "agent" and s.status == StepStatus.FAILED for s in await run.steps.alist()))

    async def test_require_tool_and_agent_call_budget_are_enforced(self):
        self.profile["policy"] = {"require_tool": True, "max_tool_calls": 1}
        model = ScriptedCompletion(answer("no tool"),
            [chunk(calls=[call('{"value":"first"}', name="echo")]), chunk(finish="tool_calls")],
            [chunk(calls=[call('{"value":"second"}', name="echo", call_id="second")]), chunk(finish="tool_calls")])
        self.profile["engine_options"]['policy']['max_iterations'] = 4
        await self.setup_graph(agent_graph(), model)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        self.assertEqual(model.requests[0]["tool_choice"], "required")
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        self.assertEqual(self.calls, [{"value": "first"}])
        self.assertNotIn("tool_choice", model.requests[-1])

    async def test_successful_required_tool_records_agent_outcome(self):
        self.profile["policy"] = {"require_tool": True}
        model = ScriptedCompletion([chunk(calls=[call('{"value":"ok"}', name="echo")]),
                                   chunk(finish="tool_calls")], answer("done"))
        await self.setup_graph(agent_graph(), model)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        step = next(s for s in await run.steps.alist() if s.kind == "agent")
        self.assertEqual(step.metadata["completed_tools"], 1)

    async def test_custom_registered_engine_is_selected_without_completion(self):
        seen = []
        class WorkEngine(BaseEngine):
            def for_agent(self, definition):
                async def work(context):
                    seen.append((definition, context.run.id, context.state["agent"]["engine"]))
                    yield "custom result"
                return BaseEngine("Work", action=work)
        self.profile = {"engine": "work", "purpose": "Deterministic work", "engine_options": {'config': {'mode': 'safe'}}}
        await self.setup_graph(agent_graph(), None, handlers={"agent": AgentNode(engines={"work": WorkEngine()})})
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(await self.output(run), {"answer": "custom result"})
        self.assertEqual(seen[0][1:], (run.id, "work"))

    async def test_agent_timeout_closes_running_tool(self):
        closed = asyncio.Event()
        async def slow(arguments):
            try:
                await asyncio.sleep(10)
            finally:
                closed.set()
        self.catalog = ToolRegistry((Tool("echo", "slow", {"type": "object"}, slow),
                                     self.catalog.get("forbidden")))
        self.profile["policy"] = {"timeout_seconds": 1}
        model = ScriptedCompletion([chunk(calls=[call('{}', name="echo")]), chunk(finish="tool_calls")])
        await self.setup_graph(agent_graph(), model)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertTrue(closed.is_set())
        self.assertTrue(all(s.status not in (StepStatus.RUNNING, StepStatus.PENDING) for s in await run.steps.alist()))

    async def test_mcp_alias_allowlist_and_connection_lifecycle(self):
        connected = []
        @asynccontextmanager
        async def connector(definition):
            connected.append(definition)
            try:
                yield self.catalog
            finally:
                connected.append("closed")
        self.profile["tools"] = []
        self.profile["resources"] = {"mcp": {"docs": {"read_docs": "echo"}}}
        self.profile["policy"] = {"require_tool": True}
        model = ScriptedCompletion([chunk(calls=[call('{"value":"remote"}', name="read_docs")]),
                                   chunk(finish="tool_calls")], answer("done"))
        await self.setup_graph(agent_graph(), model, extra_components=(MCPComponent(connector=connector),))
        mcp = await self.project.components.aget("mcp")
        await mcp.acreate({"transport": "streamable_http", "url": "https://example.invalid/mcp"}, identifier="docs")
        self.assertEqual(connected, [])
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(connected[-1], "closed")
        self.assertEqual(len(connected), 2)
        self.assertEqual([t["function"]["name"] for t in model.requests[0]["tools"]], ["read_docs"])
        self.assertEqual(self.calls, [{"value": "remote"}])

    async def test_mcp_without_adapter_fails_before_workflow_side_effects(self):
        self.profile["resources"] = {"mcp": {"docs": {"read": "echo"}}}
        await self.setup_graph(agent_graph(), ScriptedCompletion(), extra_components=(MCPComponent(),))
        await (await self.project.components.aget("mcp")).acreate(
            {"transport": "stdio", "command": "must-not-run"}, identifier="docs")
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(await run.steps.alist(), [])

    async def test_mcp_cancel_closes_tool_and_session_once(self):
        entered, closed = asyncio.Event(), []
        async def wait_tool(arguments):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append("tool")
        @asynccontextmanager
        async def connector(definition):
            try:
                yield ToolRegistry((Tool("wait", "wait", {"type": "object"}, wait_tool),))
            finally:
                closed.append("session")
        self.profile["resources"] = {"mcp": {"local": {"wait_remote": "wait"}}}
        model = ScriptedCompletion([chunk(calls=[call('{}', name="wait_remote")]), chunk(finish="tool_calls")])
        await self.setup_graph(agent_graph(), model, extra_components=(MCPComponent(connector=connector),))
        await (await self.project.components.aget("mcp")).acreate(
            {"transport": "stdio", "command": "host-owned"}, identifier="local")
        request = await self.session.run.submit("wait", engine="graph", engine_options={"workflow": "flow"})
        await asyncio.wait_for(entered.wait(), 10)
        await self.session.run.interrupt()
        run = await request.wait()
        self.assertEqual(run.data.status, RunStatus.INTERRUPTED)
        self.assertEqual(closed, ["tool", "session"])

    async def test_unknown_and_non_agent_engine_rejected_before_any_steps(self):
        self.profile["engine"] = "missing"
        model = ScriptedCompletion()
        await self.setup_graph(agent_graph(), model)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(await run.steps.alist(), [])
        self.profile["engine"] = "plain"
        await self.agents.asave("writer", self.profile)
        handler = self.backend.engines.resolve("graph").handlers["agent"]
        handler.engines.register("plain", BaseEngine())
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(await run.steps.alist(), [])
        self.assertEqual(model.requests, [])

    async def test_missing_skill_fails_before_model_and_unselected_resource_is_not_resolved(self):
        self.profile["resources"] = {"skills": ["missing"]}
        model = ScriptedCompletion(answer("ok"))
        await self.setup_graph(agent_graph(), model, extra_components=(SkillComponent(), MCPComponent()))
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        self.assertEqual(model.requests, [])
        self.profile["resources"] = {}
        await self.agents.asave("writer", self.profile)
        # MCP 연결기가 없어도 참조하지 않은 컴포넌트는 실행에 영향을 주지 않는다.
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)

    @unittest.skipUnless(all(importlib.util.find_spec(n) for n in ("chromadb", "kuzu", "rank_bm25")),
                         "Optional RAG dependencies are not installed")
    async def test_rag_agent_code_validation_repair_full_integration(self):
        rag = RAGComponent(embedding=EmbeddingModel(model="test/embed", embedding_fn=fake_embedding), extractor=Extractor())
        sources = ["def add(a, b)\n    return a + b\n", "def add(a, b):\n    return a - b\n",
                   "def add(a, b):\n    return a + b\n"]
        responses = []
        for source in sources:
            responses.extend([[chunk(calls=[call('{"query":"Alice","method":"hybrid","limit":1}', name="rag_search")]),
                               chunk(finish="tool_calls")], answer(json.dumps({"code": source}))])
        model = ScriptedCompletion(*responses)
        self.profile.update(tools=[], resources={"rag": True}, engine_options={'policy': {'max_iterations': 2}})
        graph = coding_workflow()
        graph["nodes"]["repair"]["body"]["nodes"]["implement"]["agent"] = "writer"
        graph["outputs"] = {"code": "/code", "passed": "/passed", "history": "/history"}
        output = self.root / "solution.py"
        checks = CodeChecks(output)
        await self.setup_graph(graph, model, handlers={"agent": AgentNode(engines={"loop": LoopEngine(completion_fn=model)}),
            "persist_code": checks.write, "static_check": checks.static, "test_code": checks.tests,
            "report": report}, extra_components=[rag])
        await (await self.project.components.aget("rag")).aadd_document(
            identifier="manual", title="API manual", content="## API\n\nAlice owns Atlas\n\nImplement add(a, b) as a + b.")
        run = await self.run_graph("Implement the documented API")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        result = await self.output(run)
        self.assertTrue(result["passed"])
        self.assertEqual(output.read_text(encoding="utf-8"), sources[-1])
        self.assertEqual([(item["phase"], item["ok"]) for item in result["history"]],
                         [("static", False), ("static", True), ("tests", False), ("static", True), ("tests", True)])
        for index in (1, 3, 5):
            evidence = json.loads(model.requests[index]["messages"][-1]["content"])
            self.assertEqual(evidence["documents"][0]["document_id"], "manual")
            self.assertEqual(evidence["relations"][0]["evidence"], "Alice owns Atlas")
        self.assertIn('"phase": "static"', model.requests[2]["messages"][-1]["content"])
        self.assertIn('"phase": "tests"', model.requests[4]["messages"][-1]["content"])
        steps = await run.steps.alist()
        self.assertEqual(len([s for s in steps if s.kind == "tool" and s.name == "rag_search"]), 3)
        self.assertEqual(len([s for s in steps if s.kind == "llm"]), 6)
        self.assertTrue(all(s.run_id == run.id and s.status == StepStatus.COMPLETED for s in steps))
        root_step = next(s for s in steps if s.kind == "graph")
        persisted = json.loads((root_step.paths.root / "step.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["metadata"]["output"]["data"], result)
        self.assertEqual(len(await self.session.run.alist()), 1)


class BindingTests(unittest.TestCase):
    def test_pointer_escaping_null_and_independent_copy(self):
        source = {"a/b": {"~": [None, {"x": []}]}}
        result = bind({"empty": "/a~1b/~0/0", "data": "/a~1b/~0/1"}, source)
        self.assertIsNone(result["empty"])
        result["data"]["x"].append(1)
        self.assertEqual(source["a/b"]["~"][1]["x"], [])
        with self.assertRaises(KeyError):
            bind({"bad": "/a~1b/~0/01"}, source)

    def test_invalid_mappings_schemas_and_control_bindings_rejected_on_save(self):
        for options in ({"inputs": {"x": "not-pointer"}}, {"outputs": {"x": "/bad~2"}},
                        {"input_schema": {"$ref": "https://example.com/schema"}},
                        {"output_schema": {"type": "unknown"}}):
            with self.subTest(options=options), self.assertRaises(Exception):
                graph = agent_graph()
                graph["nodes"]["agent"].update(options)
                validate_graph(graph)
        with self.assertRaises(ValueError):
            WorkflowGraph(entry="end").node("end", "end", inputs={}).to_dict()
