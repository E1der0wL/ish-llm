"""실제 LangGraph 스케줄러와 기존 Workflow/Run/Step 계약의 경계를 검증한다."""

import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from langgraph.config import get_config

from llm.components.workflows import WorkflowGraph
from llm.core.models import RunStatus, StepStatus
from llm.engines.graph import GraphEngine
from llm.llm import LargeLanguageModel
from tests.llm.test_graph_engine import parallel, straight


class LangGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def setup_graph(self, graph, work, **options):
        self.app = LargeLanguageModel(self.root, engines={"graph": GraphEngine(
            handlers={"work": work})})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("test", components=["workflows"], config={"parameters": {
            "engines": {"graph": GraphEngine.parameter_layout.pack(options)}}})
        self.workflows = await self.project.components.aget("workflows")
        await self.workflows.acreate(graph, identifier="flow")
        self.session = await self.project.sessions.acreate()

    async def run_graph(self, session=None):
        return await (await (session or self.session).run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait(timeout=30)

    async def output(self, run):
        return next(s for s in await run.steps.alist() if s.kind == "graph").output.data

    async def test_handlers_run_as_distinct_native_nodes_and_reserved_ids_are_preserved(self):
        called = []
        async def work(node):
            config = get_config()
            called.append((node.node_id, config["metadata"]["langgraph_node"]))
            return {"done": node.node_id}
        graph = (WorkflowGraph(entry="__start__").node("__start__", "work").node("with:colon", "work")
                 .node("__end__", "end").connect("__start__", "with:colon")
                 .connect("with:colon", "__end__").to_dict())
        await self.setup_graph(graph, work)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual([item[0] for item in called], ["__start__", "with:colon"])
        self.assertEqual(len({item[1] for item in called}), 2)
        self.assertTrue(all(item[1].startswith("node_") for item in called))
        root = next(s for s in await run.steps.alist() if s.kind == "graph")
        self.assertEqual(root.metadata["runtime"], "langgraph")
        self.assertEqual(root.metadata["definition"], graph)
        self.assertEqual(root.metadata["visited_nodes"], 3)

    async def test_unequal_parallel_paths_wait_once_at_join(self):
        called = []
        async def work(node):
            await asyncio.sleep(0.01 if node.node_id == "b" else 0)
            called.append(node.node_id)
            return {"seen": node.state.get("seen", []) + [node.node_id]}
        graph = (WorkflowGraph(entry="fork", initial_state={"seen": []})
                 .node("fork", "parallel", join="join")
                 .node("a", "work").node("a2", "work").node("b", "work")
                 .node("join", "join").node("after", "work").node("end", "end")
                 .connect("fork", "a").connect("fork", "b").connect("a", "a2")
                 .connect("a2", "join").connect("b", "join").connect("join", "after")
                 .connect("after", "end").to_dict())
        await self.setup_graph(graph, work)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        output = await self.output(run)
        self.assertEqual(output["seen"], ["after"])
        self.assertEqual(output["branches"]["a"]["seen"], ["a", "a2"])
        self.assertEqual(output["branches"]["b"]["seen"], ["b"])
        self.assertEqual(called[-1], "after")
        self.assertEqual(len([s for s in await run.steps.alist() if s.name == "join"]), 1)

    async def test_nested_parallel_preserves_branch_state_and_handler_concurrency_limit(self):
        active = peak = 0
        async def work(node):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                return {"value": node.node_id}
            finally:
                active -= 1
        graph = (WorkflowGraph(entry="outer")
                 .node("outer", "parallel", join="outer_join")
                 .node("inner", "parallel", join="inner_join")
                 .node("a", "work").node("b", "work").node("c", "work")
                 .node("inner_join", "join").node("outer_join", "join").node("end", "end")
                 .connect("outer", "inner").connect("outer", "c")
                 .connect("inner", "a").connect("inner", "b")
                 .connect("a", "inner_join").connect("b", "inner_join")
                 .connect("inner_join", "outer_join").connect("c", "outer_join")
                 .connect("outer_join", "end").to_dict())
        await self.setup_graph(graph, work, max_parallelism=1)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        branches = (await self.output(run))["branches"]
        self.assertEqual(branches["c"]["value"], "c")
        self.assertEqual(branches["inner"]["branches"]["a"]["value"], "a")
        self.assertEqual(branches["inner"]["branches"]["b"]["value"], "b")
        self.assertEqual(peak, 1)
        self.assertEqual(active, 0)

    async def test_repeated_parallel_resets_runtime_state_and_keeps_state_inside_each_branch(self):
        observed = []
        async def work(node):
            count = node.context.state.get("counter", 0) + 1
            node.context.state["counter"] = count
            observed.append((node.node_id, count))
            return {"count": count}
        body = (WorkflowGraph(entry="fork").node("fork", "parallel", join="join")
                .node("a", "work").node("a2", "work").node("b", "work")
                .node("join", "join").node("end", "end")
                .connect("fork", "a").connect("fork", "b").connect("a", "a2")
                .connect("a2", "join").connect("b", "join").connect("join", "end").to_dict())
        graph = (WorkflowGraph(entry="repeat").node("repeat", "loop", body=body,
                 max_iterations=2, on_limit="fail").node("end", "end").connect("repeat", "end").to_dict())
        await self.setup_graph(graph, work)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual([count for name, count in observed if name == "a"], [1, 1])
        self.assertEqual([count for name, count in observed if name == "a2"], [2, 2])
        self.assertEqual([count for name, count in observed if name == "b"], [1, 1])
        steps = await run.steps.alist()
        paths = [s.metadata["path"] for s in steps if s.name == "a2"]
        self.assertEqual(paths, ["flow/repeat[1]/fork/a2", "flow/repeat[2]/fork/a2"])
        self.assertEqual((await self.output(run))["branches"]["a"]["count"], 2)

    async def test_long_loop_uses_configured_budget_instead_of_native_default_recursion(self):
        async def work(node):
            return {"count": node.state.get("count", 0) + 1}
        graph = (WorkflowGraph(entry="repeat").node("repeat", "loop", body=straight(),
                 max_iterations=30, on_limit="fail").node("end", "end").connect("repeat", "end").to_dict())
        await self.setup_graph(graph, work)
        await self.project.asave(config={"parameters": {"engines": {"graph": {'policy': {'max_steps': 62}}}}})
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await self.output(run))["count"], 30)
        await self.project.asave(config={"parameters": {"engines": {"graph": {'policy': {'max_steps': 61}}}}})
        failed = await self.run_graph()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertIn("execution limit", failed.data.error)

    async def test_parallel_budget_is_shared_and_node_errors_are_not_retried(self):
        called = []
        async def work(node):
            called.append(node.node_id)
            return {}
        await self.setup_graph(parallel(), work, max_steps=3)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertIn("execution limit", run.data.error)
        self.assertCountEqual(called, ["a", "b"])
        called.clear()
        async def fail(node):
            called.append(node.node_id)
            raise ConnectionError("no automatic retry")
        self.app.engines.get("graph").handlers["work"] = fail
        await self.workflows.asave("flow", straight())
        failed = await self.run_graph()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertEqual(called, ["work"])

    async def test_parallel_self_cancellation_interrupts_and_drains_other_branch(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def work(node):
            if node.node_id == "a":
                await entered.wait()
                raise asyncio.CancelledError()
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                closed.set()
        await self.setup_graph(parallel(), work)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.INTERRUPTED, run.data.error)
        self.assertTrue(closed.is_set())
        self.assertTrue(all(s.status not in (StepStatus.PENDING, StepStatus.RUNNING)
                            for s in await run.steps.alist()))

    async def test_parallel_node_timeout_drains_children_and_fails_run(self):
        started, closed = set(), set()
        async def work(node):
            started.add(node.node_id)
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                closed.add(node.node_id)
        graph = parallel()
        graph["nodes"]["fork"]["timeout_seconds"] = 0.3
        await self.setup_graph(graph, work)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(started, {"a", "b"})
        self.assertEqual(closed, started)

    async def test_native_interrupt_does_not_silently_complete_or_replay(self):
        from langgraph.types import interrupt
        called = []
        async def work(node):
            called.append(node.node_id)
            interrupt("approval")
            return {"should_not": "execute"}
        await self.setup_graph(straight(), work)
        run = await self.run_graph()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertIn("explicit resume integration", run.data.error)
        self.assertEqual(called, ["work"])
        self.assertFalse(any(s.name == "end" for s in await run.steps.alist()))

    async def test_explicit_interrupt_drains_parallel_handlers_before_next_request(self):
        entered, closed, ready = set(), set(), asyncio.Event()
        async def work(node):
            message = next(m for m in node.context.messages if m.id == node.context.run.input_message_id)
            if message.content == "first":
                entered.add(node.node_id)
                if len(entered) == 2:
                    ready.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(0.01)
                    closed.add(node.node_id)
            else:
                self.assertEqual(closed, {"a", "b"})
            return {}
        await self.setup_graph(parallel(), work)
        first = await self.session.run.submit("first", engine="graph", engine_options={"workflow": "flow"})
        await asyncio.wait_for(ready.wait(), 5)
        second = await self.session.run.submit("second", engine="graph", engine_options={"workflow": "flow"})
        await self.session.run.interrupt()
        self.assertEqual((await first.wait(timeout=10)).data.status, RunStatus.INTERRUPTED)
        self.assertEqual(closed, {"a", "b"})
        self.assertEqual((await second.wait(timeout=10)).data.status, RunStatus.COMPLETED)

    async def test_same_engine_concurrent_sessions_have_separate_budgets_and_state(self):
        entered, release = set(), asyncio.Event()
        async def work(node):
            entered.add(node.context.session.id)
            if len(entered) == 2:
                release.set()
            await asyncio.wait_for(release.wait(), 5)
            return {"owner": node.context.session.id}
        await self.setup_graph(straight(), work, max_steps=2)
        other = await self.project.sessions.acreate()
        runs = await asyncio.gather(self.run_graph(), self.run_graph(other))
        for session, run in zip((self.session, other), runs):
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
            self.assertEqual((await self.output(run))["owner"], session.id)


class LangGraphImportTests(unittest.TestCase):
    def test_plugin_import_does_not_import_or_start_langgraph(self):
        result = subprocess.run([sys.executable, "-c",
            "import sys; from llm.llm import LargeLanguageModel, GraphEngine; "
            "assert not any(n.startswith(('langgraph', 'langchain', 'langsmith')) for n in sys.modules)"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
