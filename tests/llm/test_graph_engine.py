"""GraphEngine의 실제 Run/Step 저장, 분기/반복/병렬 및 코드 수정 예제를 검증한다."""

import asyncio
from pathlib import Path
import tempfile
import unittest

from examples.llm.code_workflow import DemonstrationCompletion, inspect_source, run_demo
from llm.components.workflows import WorkflowGraph
from llm.core.models import RunStatus, StepStatus
from llm.engines.graph import GraphEngine
from llm.engines.graph.engine import matches
from llm.llm import LargeLanguageModel


def straight(kind="work", **options):
    return (WorkflowGraph(entry="work").node("work", kind, **options).node("end", "end")
            .connect("work", "end").to_dict())


def parallel():
    return (WorkflowGraph(entry="fork", initial_state={"seed": []})
            .node("fork", "parallel", join="join")
            .node("a", "work").node("b", "work").node("join", "join").node("end", "end")
            .connect("fork", "a").connect("fork", "b")
            .connect("a", "join").connect("b", "join").connect("join", "end").to_dict())


class GraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def setup_graph(self, graph, handlers, **options):
        self.app = LargeLanguageModel(self.root, engines={"graph": GraphEngine(handlers=handlers)})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("graph", components=["workflows"], config={
            "parameters": {"engines": {"graph": GraphEngine.parameter_layout.pack(options)}}})
        self.workflows = await self.project.components.aget("workflows")
        await self.workflows.acreate(graph, identifier="flow")
        self.session = await self.project.sessions.acreate("session")

    async def run_graph(self):
        return await asyncio.wait_for((await self.session.run.submit("request", engine="graph", engine_options={"workflow": "flow"})).wait(), 10)

    async def output(self, run):
        root = next(step for step in await run.steps.alist() if step.kind == "graph")
        return root.output.data

    async def test_state_results_snapshot_and_domain_boundaries(self):
        async def work(node):
            return {"value": node.context.messages[-1].content, "extension": [1, None]}
        graph = straight()
        await self.setup_graph(graph, {"work": work})
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        self.assertEqual((await self.output(run))["value"], "request")
        steps = await run.steps.alist()
        root = next(step for step in steps if step.kind == "graph")
        self.assertEqual(root.metadata["definition"], graph)
        self.assertEqual(root.metadata["visited_nodes"], 2)
        self.assertTrue(all(step.run_id == run.id and step.status == StepStatus.COMPLETED for step in steps))
        self.assertEqual(len(await self.session.run.alist()), 1)

    async def test_branch_uses_first_matching_case_and_default(self):
        called = []
        async def work(node):
            called.append(node.node_id)
            return {}
        graph = (WorkflowGraph(entry="choose", initial_state={"x": 1})
                 .node("choose", "branch", cases=[
                     {"port": "first", "when": {"path": "/x", "op": "eq", "value": 1}},
                     {"port": "second", "when": {"path": "/x", "op": "exists"}},
                 ], default="other")
                 .node("first", "work").node("second", "work").node("other", "work").node("end", "end")
                 .connect("choose", "first", port="first").connect("choose", "second", port="second")
                 .connect("choose", "other", port="other").connect("first", "end")
                 .connect("second", "end").connect("other", "end").to_dict())
        await self.setup_graph(graph, {"work": work})
        await self.run_graph()
        graph["initial_state"] = {}
        await self.workflows.asave("flow", graph)
        await self.run_graph()
        self.assertEqual(called, ["first", "other"])

    async def test_bounded_loop_feedback_and_early_success(self):
        async def work(node):
            n = node.state.get("n", 0) + 1
            return {"n": n, "passed": n == 2}
        graph = (WorkflowGraph(entry="loop", initial_state={"passed": False})
                 .node("loop", "loop", body=straight(), max_iterations=3, on_limit="fail",
                       **{"while": {"path": "/passed", "op": "eq", "value": False}})
                 .node("end", "end").connect("loop", "end").to_dict())
        await self.setup_graph(graph, {"work": work})
        run = await self.run_graph()
        self.assertEqual((await self.output(run))["n"], 2)
        loop = next(step for step in await run.steps.alist() if step.name == "loop")
        self.assertEqual(loop.metadata["iterations"], 2)

    async def test_fixed_loop_finishes_at_limit_and_conditional_limit_fails(self):
        async def work(node):
            return {"n": node.state.get("n", 0) + 1}
        graph = (WorkflowGraph(entry="loop")
                 .node("loop", "loop", body=straight(), max_iterations=2, on_limit="fail")
                 .node("end", "end").connect("loop", "end").to_dict())
        await self.setup_graph(graph, {"work": work})
        run = await self.run_graph()
        self.assertEqual((await self.output(run))["n"], 2)
        graph["nodes"]["loop"]["while"] = {"path": "", "op": "exists"}
        await self.workflows.asave("flow", graph)
        failed = await self.run_graph()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertIn("iteration limit", failed.data.error)
        graph["nodes"]["loop"]["on_limit"] = "continue"
        await self.workflows.asave("flow", graph)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.COMPLETED)

    async def test_parallel_runs_concurrently_and_joins_isolated_states(self):
        entered, release = set(), asyncio.Event()
        async def work(node):
            entered.add(node.node_id)
            if len(entered) == 2:
                release.set()
            await asyncio.wait_for(release.wait(), 3)
            node.state["seed"].append(node.node_id)
            return {"seed": node.state["seed"]}
        await self.setup_graph(parallel(), {"work": work})
        output = await self.output(await self.run_graph())
        self.assertEqual(output["seed"], [])
        self.assertEqual(output["branches"]["a"]["seed"], ["a"])
        self.assertEqual(output["branches"]["b"]["seed"], ["b"])

    async def test_parallel_failure_cancels_and_drains_siblings(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def work(node):
            if node.node_id == "a":
                await entered.wait()
                raise ValueError("node failed")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        await self.setup_graph(parallel(), {"work": work})
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertTrue(closed.is_set())
        self.assertTrue(all(step.status not in (StepStatus.RUNNING, StepStatus.PENDING) for step in await run.steps.alist()))

    async def test_explicit_interrupt_preserves_next_request(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        calls = 0
        async def work(node):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            return {"ok": True}
        await self.setup_graph(straight(), {"work": work})
        first = await self.session.run.submit("first", engine="graph", engine_options={"workflow": "flow"})
        await asyncio.wait_for(entered.wait(), 3)
        second = await self.session.run.submit("second", engine="graph", engine_options={"workflow": "flow"})
        await self.session.run.interrupt()
        self.assertEqual((await first.wait()).data.status, RunStatus.INTERRUPTED)
        self.assertEqual((await asyncio.wait_for(second.wait(), 5)).data.status, RunStatus.COMPLETED)
        self.assertTrue(closed.is_set())

    async def test_node_timeout_and_execution_budget_are_terminal(self):
        async def work(node):
            await asyncio.sleep(1)
            return {}
        await self.setup_graph(straight(timeout_seconds=0.02), {"work": work}, max_steps=1)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        async def fast(node):
            return {}
        self.app.engines.get("graph").handlers["work"] = fast
        await self.workflows.asave("flow", straight())
        failed = await self.run_graph()
        self.assertIn("execution limit", failed.data.error)

    async def test_all_node_registrations_are_checked_before_side_effects(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        graph = (WorkflowGraph(entry="work").node("work", "work").node("bad", "missing").node("end", "end")
                 .connect("work", "bad").connect("bad", "end").to_dict())
        await self.setup_graph(graph, {"work": work})
        failed = await self.run_graph()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertEqual(calls, [])
        self.assertEqual(await failed.steps.alist(), [])

    async def test_handler_preflight_and_json_result_validation(self):
        class Handler:
            def validate(self, node, context):
                if node.get("resource") != "ready":
                    raise ValueError("missing resource")
            async def __call__(self, node):
                return {"invalid": object()}
        await self.setup_graph(straight(), {"work": Handler()})
        self.assertEqual((await self.run_graph()).data.status, RunStatus.FAILED)
        await self.workflows.asave("flow", straight(resource="ready"))
        failed = await self.run_graph()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertTrue(any(step.status == StepStatus.FAILED for step in await failed.steps.alist()))

    async def test_unexpected_handler_cancellation_does_not_hang(self):
        async def work(node):
            raise asyncio.CancelledError()
        await self.setup_graph(straight(), {"work": work})
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.INTERRUPTED)

    async def test_step_start_is_persisted_before_handler_side_effect(self):
        async def work(node):
            # emit은 서비스를 거친 Step 저장 확인 뒤 처리기를 진행시킨다.
            steps = self.app.step_manager.list(node.context.run)
            self.assertTrue(any(step.name == "work" and step.status == StepStatus.RUNNING for step in steps))
            return {}
        await self.setup_graph(straight(), {"work": work}, buffer_size=1)
        self.assertEqual((await self.run_graph()).data.status, RunStatus.COMPLETED)


class CodingWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_file_static_checks_failed_tests_repair_and_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            completion = DemonstrationCompletion()
            result = await run_demo(Path(root), completion_fn=completion, display=False)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["mode"], "scripted")
            self.assertEqual(result["output"]["attempt"], 3)
            self.assertEqual([(item["phase"], item["ok"]) for item in result["output"]["history"]],
                             [("static", False), ("static", True), ("tests", False), ("static", True), ("tests", True)])
            self.assertEqual(Path(result["file"]).read_text(encoding="utf-8"), "def add(a, b):\n    return a + b\n")
            self.assertEqual(len(completion.requests), 3)
            self.assertIn('"phase": "static"', completion.requests[1]["messages"][-1]["content"])
            self.assertIn('"phase": "tests"', completion.requests[2]["messages"][-1]["content"])
            self.assertTrue((Path(result["project"]) / "workflows" / "records" / "code-review.json").is_file())

    async def test_iteration_exhaustion_does_not_report_success(self):
        with tempfile.TemporaryDirectory() as root:
            result = await run_demo(Path(root), max_attempts=2, display=False)
            self.assertEqual(result["status"], "failed")
            self.assertIn("iteration limit", result["error"])

    def test_static_contract_rejects_inappropriate_implementations(self):
        for source in ("import os\ndef add(a,b): return a+b", "def add(a,b): return open('file')",
                       "def add(a,b):\n while True: pass", "def add(a,b): return 'hardcoded'",
                       "def add(a,b): return __import__('os')", "@print\ndef add(a,b): return a+b"):
            self.assertTrue(inspect_source(source))
        self.assertEqual(inspect_source("def add(a,b): return a+b"), [])


class ConditionTests(unittest.TestCase):
    def test_json_pointer_missing_values_types_and_operators(self):
        state = {"a/b": {"~key": [3]}, "flag": True, "nested": {"x": [True]}}
        self.assertTrue(matches({"path": "/a~1b/~0key/0", "op": "ge", "value": 3}, state))
        self.assertFalse(matches({"path": "/missing", "op": "ne", "value": 0}, state))
        self.assertFalse(matches({"path": "/flag", "op": "eq", "value": 1}, state))
        self.assertFalse(matches({"path": "/nested", "op": "eq", "value": {"x": [1]}}, state))
        self.assertFalse(matches({"path": "/a~1b/~0key/00", "op": "exists"}, state))
        self.assertTrue(matches({"path": "/flag", "op": "in", "value": [True]}, state))
        with self.assertRaises(RuntimeError):
            matches({"path": "/flag", "op": "lt", "value": 2}, state)


if __name__ == "__main__":
    unittest.main()
