"""요청별 엔진 선택값의 저장·격리·복구 및 Graph 재개 계약."""

import asyncio
import tempfile
import unittest
from copy import copy
from pathlib import Path

from llm.core.models import RunStatus
from llm.core.steering import SteeringMode
from llm.components.workflows import WorkflowGraph
from llm.engines.base import BaseEngine
from llm.engines.graph import GraphEngine
from llm.engines.registry import EngineRegistry
from llm.llm import LargeLanguageModel
from llm.services.runtime.runs import RunRequestError


def graph(tag, *, pause=False):
    return (WorkflowGraph(entry="work", initial_state={"tag": tag})
            .node("work", "work", pause_before=pause).node("end", "end")
            .connect("work", "end").to_dict())


class RequestBindingTests(unittest.TestCase):
    def test_graph_requires_explicit_workflow_without_mutating_template(self):
        engine = GraphEngine(handlers={})
        registry = EngineRegistry()
        registry.register("graph", engine)
        for options in ({}, {"workflow": None}, {"workflow": " "}, {"workflow": 1},
                        {"workflow": "a", "timeout_seconds": 10}, None):
            with self.subTest(options=options), self.assertRaises((TypeError, ValueError)):
                registry.resolve_request("graph", options)
        first = registry.resolve_request("graph", {"workflow": "a"})
        second = registry.resolve_request("graph", {"workflow": "b"})
        self.assertEqual((first.workflow, second.workflow, engine.workflow), ("a", "b", None))
        with self.assertRaises(TypeError):
            GraphEngine("a", handlers={})
        with self.assertRaisesRegex(ValueError, "workflow"):
            engine.for_agent({"engine": "graph"})

    def test_custom_engine_hook_and_unhandled_options(self):
        class Custom(BaseEngine):
            def for_request(self, options):
                options["label"].append("bound")
                worker = copy(self)
                worker.label = options["label"]
                return worker
        registry = EngineRegistry()
        registry.register("custom", Custom())
        registry.register("plain", BaseEngine())
        source = {"label": ["user"]}
        worker = registry.resolve_request("custom", source)
        self.assertEqual(source, {"label": ["user"]})
        self.assertEqual(worker.label, ["user", "bound"])
        self.assertIs(registry.resolve_request("plain", {}), registry.get("plain"))
        with self.assertRaisesRegex(ValueError, "does not support"):
            registry.resolve_request("plain", {"workflow": "a"})

    def test_async_factory_is_rejected_without_leaking_coroutine(self):
        class Invalid(BaseEngine):
            async def for_request(self, options):
                return self
        registry = EngineRegistry()
        registry.register("invalid", Invalid())
        with self.assertRaisesRegex(TypeError, "synchronous"):
            registry.resolve_request("invalid", {})

    def test_factory_cannot_change_checkpoint_or_instruction_contract(self):
        class Invalid(BaseEngine):
            def for_request(self, options):
                worker = copy(self)
                setattr(worker, options["key"], options["value"])
                return worker
        registry = EngineRegistry()
        registry.register("invalid", Invalid())
        for key, value in (("checkpoint_name", "other"), ("steering_mode", SteeringMode.CONSUME)):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "preserve"):
                registry.resolve_request("invalid", {"key": key, "value": value})


class GraphRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.effects = []

    async def setup_backend(self, work=None):
        async def default(node):
            self.effects.append(node.state["tag"])
            return {"observed": node.state["tag"]}
        self.engine = GraphEngine(handlers={"work": work or default})
        self.app = LargeLanguageModel(self.root, engines={"graph": self.engine})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=["workflows"])
        self.workflows = await self.project.components.aget("workflows")
        await self.workflows.acreate(graph("a"), identifier="a")
        await self.workflows.acreate(graph("b"), identifier="b")
        self.session = await self.project.sessions.acreate()

    async def reopen(self):
        await self.app.shutdown()
        app = LargeLanguageModel(self.root, engines={"graph": self.engine})
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.aload(self.project.id)
        return await project.sessions.aload(self.session.id)

    async def test_invalid_options_rejected_before_queue_and_missing_id_before_effects(self):
        await self.setup_backend()
        for kwargs in ({}, {"engine_options": None}, {"engine_options": {}},
                       {"engine_options": {"workflow": ""}}, {"engine_options": {"workflow": object()}}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                await self.session.run.submit("go", engine="graph", **kwargs)
        self.assertEqual(await self.session.run.alist(), [])
        run = await (await self.session.run.submit("go", engine="graph", engine_options={"workflow": "missing"})).wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(await run.steps.alist(), [])
        self.assertEqual(self.effects, [])

    async def test_two_sessions_share_template_but_not_workflow_selection(self):
        reached, release = set(), asyncio.Event()
        async def work(node):
            reached.add(node.state["tag"])
            if len(reached) == 2:
                release.set()
            await asyncio.wait_for(release.wait(), 5)
            return {"observed": node.state["tag"]}
        await self.setup_backend(work)
        other = await self.project.sessions.acreate()
        options = {"workflow": "a"}
        first = await self.session.run.submit("one", engine="graph", engine_options=options)
        options["workflow"] = "b"
        second = await other.run.submit("two", engine="graph", engine_options=options)
        runs = await asyncio.gather(first.wait(timeout=10), second.wait(timeout=10))
        for run, expected in zip(runs, ("a", "b")):
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
            self.assertEqual(run.data.metadata["engine_options"], {"workflow": expected})
            self.assertEqual((await run.acheckpoint())["header"]["binding"]["workflow_id"], expected)
        self.assertIsNone(self.engine.workflow)

    async def test_queued_selection_survives_shutdown_and_reopen(self):
        entered = asyncio.Event()
        async def work(node):
            self.effects.append(node.state["tag"])
            if node.state["tag"] == "a":
                entered.set()
                await asyncio.Event().wait()
            return {}
        await self.setup_backend(work)
        await self.session.run.submit("one", engine="graph", engine_options={"workflow": "a"})
        await asyncio.wait_for(entered.wait(), 5)
        queued = await self.session.run.submit("two", engine="graph", engine_options={"workflow": "b"})
        self.assertEqual((await queued.aget_data()).metadata["engine_options"], {"workflow": "b"})
        session = await self.reopen()
        await session.run.start()
        run = await session.run.request(queued.id).wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(self.effects, ["a", "b"])

    async def test_resume_restores_selection_and_rejects_changed_definition(self):
        await self.setup_backend()
        await self.workflows.asave("a", graph("a", pause=True))
        paused = await (await self.session.run.submit("go", engine="graph", engine_options={"workflow": "a"})).wait(timeout=10)
        self.assertEqual(paused.data.status, RunStatus.PAUSED)
        session = await self.reopen()
        with self.assertRaises(TypeError):
            await session.run.resume(paused.id, engine="graph", engine_options={"workflow": "b"})
        resumed = await (await session.run.resume(paused.id, engine="graph")).wait(timeout=10)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(resumed.data.metadata["engine_options"], {"workflow": "a"})
        self.assertEqual(self.effects, ["a"])
        next_pause = await (await session.run.submit("again", engine="graph", engine_options={"workflow": "a"})).wait(timeout=10)
        workflows = await session.project.components.aget("workflows")
        await workflows.asave("a", graph("changed", pause=True))
        with self.assertRaisesRegex(RunRequestError, "changed"):
            await session.run.resume(next_pause.id, engine="graph")
        self.assertEqual(self.effects, ["a"])

    async def test_queued_request_reads_definition_at_run_start(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def work(node):
            if node.state["tag"] == "a":
                entered.set()
                await release.wait()
            self.effects.append(node.state["tag"])
            return {}
        await self.setup_backend(work)
        first = await self.session.run.submit("one", engine="graph", engine_options={"workflow": "a"})
        await asyncio.wait_for(entered.wait(), 5)
        second = await self.session.run.submit("two", engine="graph", engine_options={"workflow": "b"})
        await self.workflows.asave("b", graph("updated"))
        release.set()
        await first.wait(timeout=10)
        run = await second.wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(self.effects, ["a", "updated"])
