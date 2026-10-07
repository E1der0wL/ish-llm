"""저장 Agent 위임은 같은 Run/승인/도구 예산을 사용하는지 검사한다."""

import json
import tempfile
import unittest

from llm.llm import LargeLanguageModel, ServiceConfig
from llm.components.agents import AgentComponent
from llm.components.tools import Tool, ToolRegistry, ToolContract, ToolClassification
from llm.engines.loop import LoopEngine
from llm.services.runtime.tools import ToolPolicy
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class AgentDelegationTests(unittest.IsolatedAsyncioTestCase):
    async def setup(self, *, child_tools=(), registry=None, policies=None, child_responses=None, definition=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        parent = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({"agent_id": "worker", "input": {"task": "work"}}), name="agent_run")], finish="tool_calls")],
            [chunk("parent done", finish="stop")])
        child = ScriptedCompletion(*(child_responses or [[chunk("child done", finish="stop")]]))
        engines = {"parent": LoopEngine(completion_fn=parent), "child": LoopEngine(completion_fn=child)}
        components = [AgentComponent(engines=engines)]
        if registry:
            components.append(RuntimeTools(registry))
        app = LargeLanguageModel(directory.name, engines=engines, components=components)
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate("delegate", components=[c.name for c in components], config={
            "policies": policies or {}, "parameters": {"engines": {
                name: {"config": {"completion": {"model": "test/" + name}}} for name in engines}}})
        await project.components.agents.acreate({"purpose": "work", "engine": "child", "tools": list(child_tools),
                                                **(definition or {})}, identifier="worker")
        if registry:
            await project.components.tools.aenable(*registry.names())
        session = await project.sessions.acreate("one")
        return app, session, parent, child

    async def test_loop_delegates_same_run_and_steps(self):
        app, session, parent, child = await self.setup()
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(len(await session.run.alist()), 1)
        steps = await run.steps.alist()
        agent, = [s for s in steps if s.kind == "agent"]
        self.assertTrue(all(s.run_id == run.id for s in steps))
        self.assertEqual(agent.metadata["agent_id"], "worker")
        self.assertEqual(len(child.requests), 1)
        self.assertEqual(json.loads(parent.requests[1]["messages"][-1]["content"]), {"text": "child done"})

    async def test_nested_approval_resume_reuses_parent_completion(self):
        effects = []
        async def write(args):
            effects.append(args)
            return "written"
        registry = ToolRegistry((Tool("write", "write", {"type": "object", "additionalProperties": False}, write,
            contract=ToolContract(approval_required=True), classification=ToolClassification("file.write", "test", 40)),))
        app, session, parent, child = await self.setup(child_tools=("write",), registry=registry,
            policies={"tools": {"max_calls": 2}},
            child_responses=[[chunk(calls=[call("{}", name="write")], finish="tool_calls")],
                             [chunk("child done", finish="stop")]])
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.status, "paused", run.data.error)
        self.assertEqual(effects, [])
        request, = await run.ainteractions(pending_only=True)
        self.assertEqual(request.risk, 40)
        await run.arespond(request.respond("approve"))
        resumed = await (await session.run.resume(run.id, engine="parent")).wait(timeout=10)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(effects, [{}])
        self.assertEqual(len(parent.requests), 2)
        self.assertEqual(len(child.requests), 2)
        self.assertEqual(app.observability.snapshot()["tools"]["requests"], 2)

    async def test_agent_output_contract_rejects_invalid_result(self):
        _, session, _, _ = await self.setup(definition={"output_format": "json",
            "output_schema": {"type": "object", "required": ["data"]}},
            child_responses=[[chunk('not json', finish="stop")]])
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.status, "failed")
        self.assertTrue(any(s.kind == "agent" and s.status == "failed" for s in await run.steps.alist()))

    async def test_resume_does_not_replay_completed_child_effect(self):
        effects = []
        async def work(args):
            effects.append(args["value"])
            return None
        schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
        registry = ToolRegistry((Tool("first", "first", schema, work),
            Tool("second", "second", schema, work, contract=ToolContract(approval_required=True))))
        app, session, parent, child = await self.setup(registry=registry, child_tools=registry.names(),
            policies={"tools": {"max_calls": 3}}, child_responses=[
                [chunk(calls=[call('{"value":1}', name="first")], finish="tool_calls")],
                [chunk(calls=[call('{"value":2}', name="second", call_id="second")], finish="tool_calls")],
                [chunk("done", finish="stop")]])
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.status, "paused", run.data.error)
        self.assertEqual(effects, [1])
        request, = await run.ainteractions(pending_only=True)
        await run.arespond(request.respond("approve"))
        resumed = await (await session.run.resume(run.id, engine="parent")).wait(timeout=10)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(effects, [1, 2])
        self.assertEqual((len(parent.requests), len(child.requests)), (2, 3))
        self.assertEqual(app.observability.snapshot()["tools"]["requests"], 3)

    async def test_delegation_and_child_calls_share_project_budget(self):
        effects = []
        async def write(args):
            effects.append(args)
        registry = ToolRegistry((Tool("write", "write", {"type": "object"}, write),))
        _, session, _, _ = await self.setup(child_tools=("write",), registry=registry,
            policies={"tools": {"max_calls": 1}},
            child_responses=[[chunk(calls=[call("{}", name="write")], finish="tool_calls")]])
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.error_code, "tool_budget_exceeded")
        self.assertEqual(effects, [])

    async def test_agent_timeout_stops_nested_work(self):
        import time
        def slow(**kwargs):
            time.sleep(.1)
            yield chunk("late", finish="stop")
        app, session, _, _ = await self.setup(definition={"policy": {"timeout_seconds": .01}})
        app.engines.resolve("child").completion_fn = slow
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.status, "failed")
        self.assertNotEqual(run.data.metadata.get("output", {}).get("text"), "late")

    async def test_child_cannot_widen_parent_tools(self):
        effects = []
        async def write(args):
            effects.append(args)
        registry = ToolRegistry((Tool("write", "write", {"type": "object"}, write),))
        _, session, _, child = await self.setup(child_tools=("write",), registry=registry,
            policies={"tools": {"allowed_tools": ["agent_run"]}})
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.status, "failed")
        self.assertEqual(effects, [])
        self.assertEqual(child.requests, [])

    async def test_parent_cancellation_reaches_child_tool(self):
        import asyncio
        started, stopped = asyncio.Event(), asyncio.Event()
        async def work(args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        registry = ToolRegistry((Tool("work", "work", {"type": "object"}, work),))
        _, session, _, _ = await self.setup(child_tools=("work",), registry=registry,
            child_responses=[[chunk(calls=[call("{}", name="work")], finish="tool_calls")]])
        request = await session.run.submit("go", engine="parent")
        await asyncio.wait_for(started.wait(), 10)
        await session.run.interrupt()
        run = await request.wait(timeout=10)
        self.assertEqual(run.data.status, "interrupted")
        self.assertTrue(stopped.is_set())

    async def test_child_inherits_fixed_argument_constraints(self):
        effects = []
        async def work(args):
            effects.append(args)
            return "done"
        registry = ToolRegistry((Tool("work", "work", {"type": "object", "properties": {
            "target": {"type": "string"}}}, work),))
        _, session, _, child = await self.setup(child_tools=("work",), registry=registry,
            policies={"tools": {"argument_constraints": {"work": {"target": {"mode": "fixed", "value": "safe"}}}}},
            child_responses=[[chunk(calls=[call("{}", name="work")], finish="tool_calls")],
                             [chunk("done", finish="stop")]])
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(effects, [{"target": "safe"}])
        self.assertEqual(child.requests[0]["tools"][0]["function"]["parameters"]["properties"]["target"]["allOf"][-1], {"const": "safe"})

    async def test_recursion_is_explicit_saved_agent_selection(self):
        app, session, _, child = await self.setup(child_tools=("agent_run",), child_responses=[
            [chunk(calls=[call('{"agent_id":"leaf","input":{}}', name="agent_run")], finish="tool_calls")],
            [chunk("leaf done", finish="stop")], [chunk("worker done", finish="stop")]])
        project = (await app.projects.alist())[0]
        await project.components.agents.acreate({"purpose": "leaf", "engine": "child", "tools": []}, identifier="leaf")
        run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(len(await session.run.alist()), 1)
        self.assertEqual({s.name for s in await run.steps.alist() if s.kind == "agent"}, {"worker", "leaf"})
        self.assertEqual(len(child.requests), 3)

    async def test_graph_backed_agent_runs_in_same_run(self):
        from llm.engines.graph import GraphEngine
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        parent = ScriptedCompletion([chunk(calls=[call('{"agent_id":"graph-worker","input":{"task":"x"}}', name="agent_run")], finish="tool_calls")],
                                    [chunk("done", finish="stop")])
        effects = []
        async def echo(node):
            effects.append(node.node_id)
            return {"done": True}
        engines = {"parent": LoopEngine(completion_fn=parent), "graph": GraphEngine(handlers={"echo": echo})}
        async with LargeLanguageModel(directory.name, engines=engines,
                components=[AgentComponent(engines=engines), WorkflowComponent()]) as app:
            project = await app.projects.acreate(components=["agents", "workflows"], config={"parameters": {
                "engines": {"parent": {"config": {"completion": {"model": "test/model"}}}}}})
            await project.components.workflows.acreate(WorkflowGraph(entry="echo").node("echo", "echo")
                .node("after", "echo", pause_before=True).node("end", "end")
                .connect("echo", "after").connect("after", "end").to_dict(), identifier="flow")
            await project.components.agents.acreate({"engine": "graph", "purpose": "graph",
                "engine_options": {"workflow": "flow"}}, identifier="graph-worker")
            session = await project.sessions.acreate()
            run = await (await session.run.submit("go", engine="parent")).wait(timeout=10)
            self.assertEqual(run.data.status, "paused", run.data.error)
            self.assertEqual(len(await session.run.alist()), 1)
            self.assertTrue(any(s.kind == "graph_node" for s in await run.steps.alist()))
            self.assertEqual(effects, ["echo"])
            request, = await run.ainteractions(pending_only=True)
            await run.arespond(request.respond("approve"))
            resumed = await (await session.run.resume(run.id, engine="parent")).wait(timeout=10)
            self.assertEqual(resumed.data.status, "completed", resumed.data.error)
            self.assertEqual(effects, ["echo", "after"])
            self.assertEqual(len(parent.requests), 2)
