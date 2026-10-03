"""공통 지시 계약과 실제 Graph/Agent 전달·저장·재개를 검사한다. 외부 모델은 호출하지 않는다."""

import asyncio
from copy import deepcopy
from contextlib import aclosing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel, ProjectConfig, LoopEngine, SteeringMode
from llm.components.agents import AgentComponent
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from llm.core.results import EngineOutput
from llm.services.runtime.runs import RunRequestError
from tests.llm.test_loop import chunk
from tests.llm.test_loop_steering import Model


def agent_graph(agent="writer"):
    return (WorkflowGraph(entry="work").node("work", "agent", agent=agent)
            .node("end", "end").connect("work", "end").to_dict())


def parallel_graph():
    return (WorkflowGraph(entry="fork").node("fork", "parallel", join="join")
            .node("a", "agent", agent="a").node("b", "agent", agent="b")
            .node("join", "join").node("end", "end")
            .connect("fork", "a").connect("fork", "b").connect("a", "join")
            .connect("b", "join").connect("join", "end").to_dict())


class GraphSteeringTests(unittest.IsolatedAsyncioTestCase):
    async def setup_graph(self, models, graph=None, *, options=None, graphs=None, profiles=None, handlers=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        engines = {name: LoopEngine(completion_fn=model, **(options or {}).get(name, {})) for name, model in models.items()}
        self.handler = AgentNode(engines=engines)
        self.engine = GraphEngine("flow", handlers=handlers or {"agent": self.handler})
        self.events = []
        self.app = LargeLanguageModel(self.root, engines={"graph": self.engine},
            components=[WorkflowComponent(), AgentComponent()], on_event=lambda r, e: self.events.append(e))
        self.addAsyncCleanup(self.app.shutdown)
        for model in models.values():
            self.addAsyncCleanup(self.release_model, model)
        self.project = await self.app.projects.acreate("steering", components=["workflows", "agents"])
        workflows = await self.project.components.aget("workflows")
        for name, definition in {"flow": graph or agent_graph(), **(graphs or {})}.items():
            await workflows.acreate(definition, identifier=name)
        agents = await self.project.components.aget("agents")
        for name, profile in (profiles or {name: {"purpose": "write", "engine": name,
                "completion": {"model": "test/model"}} for name in models}).items():
            await agents.acreate(profile, identifier=name)
        self.session = await self.project.sessions.acreate()
        self.request = await self.session.run.submit("original", engine="graph")

    async def release_model(self, model):
        model.release.set()

    async def entered(self, *models):
        for model in models:
            self.assertTrue(await asyncio.to_thread(model.entered.wait, 10))
        self.run = await self.request.aget_run()
        return await self.run.ainstruction_targets()

    async def completed(self):
        run = await self.request.wait(timeout=15)
        self.assertEqual(run.data.status, "completed", run.data.error)
        return run

    async def test_single_target_graph_keeps_run_and_records_input_in_parent_checkpoint(self):
        model = Model([chunk("draft", finish="stop")], [chunk("revised", finish="stop")])
        await self.setup_graph({"writer": model})
        target, = await self.entered(model)
        self.assertEqual((target.node_id, target.mode, target.accepting), ("work", "consume", True))
        with self.assertRaises(RunRequestError):
            await self.session.run.steer(self.run.id, "ambiguous")
        instruction = await self.session.run.steer(self.run.id, "correction", targets=[target.id])
        model.release.set()
        run = await self.completed()
        self.assertEqual(run.id, self.run.id)
        self.assertEqual([m["content"] for m in model.requests[1]["messages"]][-1], "correction")
        value, = await run.ainstructions()
        self.assertEqual((value.id, value.status, value.targets[0]["status"]), (instruction.id, "applied", "applied"))
        records = (await run.acheckpoint())["records"].values()
        inputs = [r for r in records if r.get("payload", {}).get("message_ids") == [instruction.id]]
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]["engine_scope"], target.scope)
        self.assertFalse((await run.ainstruction_targets())[0].accepting)
        target_events = [e for e in self.events if e.type == EngineEventType.STEERING_CHANGED
                         and "targets" in e.metadata and "instructions" not in e.metadata]
        self.assertTrue(any(t["accepting"] for e in target_events for t in e.metadata["targets"]))
        self.assertEqual(target_events[-1].metadata["targets"],
                         [t.to_dict() for t in await run.ainstruction_targets()])
        with self.assertRaises(RunRequestError):
            await self.session.run.steer(run.id, "late", targets=[target.id])

    async def test_parallel_selection_does_not_reach_unselected_agent(self):
        a = Model([chunk("a", finish="stop")], [chunk("revised", finish="stop")])
        b = Model([chunk("b", finish="stop")])
        await self.setup_graph({"a": a, "b": b}, parallel_graph())
        targets = await self.entered(a, b)
        self.assertEqual(len(targets), 2)
        chosen = next(t for t in targets if t.node_id == "a")
        await self.session.run.steer(self.run.id, "only A", targets=[chosen.id])
        a.release.set()
        b.release.set()
        await self.completed()
        self.assertEqual((len(a.requests), len(b.requests)), (2, 1))
        self.assertFalse(any(m.get("content") == "only A" for r in b.requests for m in r["messages"]))

    async def test_multicast_single_body_independent_receipts_and_partial_result(self):
        a = Model([chunk("a", finish="stop")], [chunk("revised", finish="stop")])
        b = Model([chunk("b", finish="stop")])
        await self.setup_graph({"a": a, "b": b}, parallel_graph(), options={"b": {"max_iterations": 1}})
        targets = await self.entered(a, b)
        await self.session.run.steer(self.run.id, "both", targets=[t.id for t in targets])
        a.release.set()
        b.release.set()
        run = await self.completed()
        value, = await run.ainstructions()
        self.assertEqual(value.status, "partially_applied")
        self.assertEqual({t["status"] for t in value.targets}, {"applied", "unapplied"})
        self.assertEqual(next(t["reason"] for t in value.targets if t["status"] == "unapplied"), "iteration_limit")
        messages = await self.session.aconversation()
        self.assertEqual(sum(m.content == "both" for m in messages), 1)

    async def test_invalid_or_duplicate_target_does_not_partially_accept(self):
        a = Model([chunk("a", finish="stop")])
        await self.setup_graph({"writer": a})
        target, = await self.entered(a)
        for targets in ([target.id, "missing"], [target.id, target.id], [], "root"):
            with self.subTest(targets=targets), self.assertRaises((RunRequestError, ValueError)):
                await self.session.run.steer(self.run.id, "invalid", targets=targets)
        self.assertEqual(await self.run.ainstructions(), [])
        a.release.set()
        await self.completed()

    async def test_nested_workflow_delivers_to_leaf_only(self):
        model = Model([chunk("draft", finish="stop")], [chunk("revised", finish="stop")])
        graph = (WorkflowGraph(entry="child").node("child", "workflow", workflow="nested")
                 .node("end", "end").connect("child", "end").to_dict())
        await self.setup_graph({"writer": model}, graph, graphs={"nested": agent_graph()})
        target, = await self.entered(model)
        self.assertIn("nested", target.node_path)
        await self.session.run.steer(self.run.id, "nested correction", targets=[target.id])
        model.release.set()
        await self.completed()
        self.assertEqual(model.requests[1]["messages"][-1]["content"], "nested correction")

    async def test_nested_graph_agent_exposes_forwarder_and_leaf(self):
        model = Model([chunk("draft", finish="stop")], [chunk("revised", finish="stop")])
        leaf = AgentNode(engines={"writer": LoopEngine(completion_fn=model)})
        outer = AgentNode(engines={"subgraph": GraphEngine("nested", handlers={"agent": leaf})})
        profiles = {"writer": {"purpose": "write", "engine": "writer", "completion": {"model": "test/model"}},
                    "coordinator": {"purpose": "delegate", "engine": "subgraph", "workflow": "nested"}}
        await self.setup_graph({"writer": model}, agent_graph("coordinator"), graphs={"nested": agent_graph()},
                               profiles=profiles, handlers={"agent": outer})
        targets = await self.entered(model)
        self.assertEqual({t.mode for t in targets}, {"consume", "forward"})
        target = next(t for t in targets if t.mode == "consume")
        await self.session.run.steer(self.run.id, "leaf correction", targets=[target.id])
        model.release.set()
        await self.completed()
        self.assertEqual(model.requests[1]["messages"][-1]["content"], "leaf correction")

    async def test_failure_reopen_resume_reuses_selected_instruction(self):
        model = Model([chunk("draft", finish="stop")], [ConnectionError("offline")], [chunk("done", finish="stop")])
        await self.setup_graph({"writer": model})
        target, = await self.entered(model)
        instruction = await self.session.run.steer(self.run.id, "remember", targets=[target.id])
        model.release.set()
        failed = await self.request.wait(timeout=15)
        self.assertEqual(failed.data.status, "failed")
        ids = self.project.id, self.session.id
        await self.app.shutdown()
        reopened = LargeLanguageModel(self.root, engines={"graph": self.engine}, components=[WorkflowComponent(), AgentComponent()])
        self.addAsyncCleanup(reopened.shutdown)
        session = await (await reopened.projects.aload(ids[0])).sessions.aload(ids[1])
        resumed = await (await session.run.resume(failed.id, engine="graph")).wait(timeout=15)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(len(model.requests), 3)
        self.assertEqual([m["content"] for m in model.requests[-1]["messages"]].count("remember"), 1)
        value, = await resumed.ainstructions()
        self.assertEqual(value.id, instruction.id)
        self.assertEqual([a["run_id"] for a in value.applications], [failed.id, resumed.id])
        self.assertNotEqual((await resumed.ainstruction_targets())[0].id, target.id)

    async def test_interrupt_marks_all_pending_targets_without_replaying_input(self):
        a, b = Model([chunk("a", finish="stop")]), Model([chunk("b", finish="stop")])
        await self.setup_graph({"a": a, "b": b}, parallel_graph())
        targets = await self.entered(a, b)
        await self.session.run.steer(self.run.id, "pending", targets=[t.id for t in targets])
        self.assertTrue(await self.session.run.interrupt())
        run = await self.request.wait(timeout=15)
        self.assertEqual(run.data.status, "interrupted")
        value, = await run.ainstructions()
        self.assertTrue(all(t["status"] == "unapplied" for t in value.targets))
        self.assertTrue(all(not t.accepting for t in await run.ainstruction_targets()))
        a.release.set()
        b.release.set()

    async def test_multicast_can_be_applied_to_both_scopes_without_duplicate_body(self):
        a = Model([chunk("a", finish="stop")], [chunk("revised a", finish="stop")])
        b = Model([chunk("b", finish="stop")], [chunk("revised b", finish="stop")])
        await self.setup_graph({"a": a, "b": b}, parallel_graph())
        targets = await self.entered(a, b)
        await self.session.run.steer(self.run.id, "shared", targets=[t.id for t in targets])
        a.release.set()
        b.release.set()
        run = await self.completed()
        value, = await run.ainstructions()
        self.assertEqual(value.status, "applied")
        self.assertEqual(len(value.applications), 2)
        self.assertEqual({v["target_id"] for v in value.applications}, {t.id for t in targets})
        self.assertTrue(all(len(m.requests) == 2 and m.requests[-1]["messages"][-1]["content"] == "shared" for m in (a, b)))

    async def test_repeated_node_targets_are_distinct_and_closed_target_is_not_redirected(self):
        third_entered, third_release = threading.Event(), threading.Event()
        class RepeatedModel(Model):
            def __call__(self, **kwargs):
                if len(self.requests) == 2:
                    third_entered.set()
                    if not third_release.wait(10):
                        raise TimeoutError("Third completion gate timed out")
                yield from super().__call__(**kwargs)
        model = RepeatedModel([chunk("draft", finish="stop")], [chunk("fixed", finish="stop")],
                              [chunk("next", finish="stop")])
        graph = (WorkflowGraph(entry="repeat").node("repeat", "loop", body=agent_graph(),
                 max_iterations=2, on_limit="continue").node("end", "end").connect("repeat", "end").to_dict())
        await self.setup_graph({"writer": model}, graph)
        async def release_third():
            third_release.set()
        self.addAsyncCleanup(release_third)
        first, = await self.entered(model)
        await self.session.run.steer(self.run.id, "first iteration only", targets=[first.id])
        model.release.set()
        self.assertTrue(await asyncio.to_thread(third_entered.wait, 10))
        targets = await self.run.ainstruction_targets()
        self.assertEqual(len(targets), 2)
        self.assertEqual(len({t.id for t in targets}), 2)
        self.assertEqual(len({t.scope for t in targets}), 2)
        self.assertEqual(sum(t.accepting for t in targets), 1)
        with self.assertRaises(RunRequestError):
            await self.session.run.steer(self.run.id, "late", targets=[first.id])
        third_release.set()
        await self.completed()
        self.assertFalse(any(m.get("content") == "first iteration only" for m in model.requests[2]["messages"]))

    async def test_targeted_input_is_not_reintroduced_in_followup_conversation(self):
        model = Model([chunk("draft", finish="stop")], [chunk("fixed", finish="stop")], [chunk("next", finish="stop")])
        await self.setup_graph({"writer": model})
        target, = await self.entered(model)
        await self.session.run.steer(self.run.id, "private to node", targets=[target.id])
        model.release.set()
        await self.completed()
        # 같은 저장 Session을 일반 Loop로 열어도 노드 전용 지시는 사용자 공통 문맥이 아니다.
        ids = self.project.id, self.session.id
        await self.app.shutdown()
        reopened = LargeLanguageModel(self.root, engines={"loop": LoopEngine(completion_fn=model,
                                       completion_kwargs={"model": "test/model"})})
        self.addAsyncCleanup(reopened.shutdown)
        session = await (await reopened.projects.aload(ids[0])).sessions.aload(ids[1])
        next_run = await (await session.run.submit("next", engine="loop")).wait(timeout=15)
        self.assertEqual(next_run.data.status, "completed", next_run.data.error)
        self.assertFalse(any(m.get("content") == "private to node" for m in model.requests[-1]["messages"]))

    async def test_child_checkpoint_failure_never_reaches_next_model_call(self):
        model = Model([chunk("draft", finish="stop")])
        await self.setup_graph({"writer": model})
        target, = await self.entered(model)
        await self.session.run.steer(self.run.id, "correction", targets=[target.id])
        record = self.app.run_repository.record_checkpoint
        def fail(run, event):
            value = event.metadata.get("value", {}).get("payload", {})
            record(run, event)
            if value.get("status") == "input" and value.get("message_ids"):
                raise OSError("checkpoint commit failed")
        with patch.object(self.app.run_repository, "record_checkpoint", side_effect=fail):
            model.release.set()
            run = await self.request.wait(timeout=15)
        self.assertEqual(run.data.status, "failed")
        self.assertEqual(len(model.requests), 1)
        value, = await run.ainstructions()
        self.assertEqual((value.status, value.applications), ("unapplied", []))
        records = (await run.acheckpoint())["records"].values()
        self.assertFalse(any(r.get("payload", {}).get("message_ids") == [value.id] for r in records))

    async def test_not_started_and_unsupported_engine_are_not_accepting_targets(self):
        started, release = asyncio.Event(), asyncio.Event()
        class Unsupported(BaseEngine):
            def for_agent(self, definition):
                return self
            async def run(self, context):
                started.set()
                await release.wait()
                yield EngineOutput(text="done")
        graph = agent_graph()
        handlers = {"agent": AgentNode(engines={"plain": Unsupported()})}
        await self.setup_graph({}, graph, handlers=handlers,
            profiles={"writer": {"purpose": "plain", "engine": "plain"}})
        async def release_child():
            release.set()
        self.addAsyncCleanup(release_child)
        await asyncio.wait_for(started.wait(), 10)
        run = await self.request.aget_run()
        target, = await run.ainstruction_targets()
        self.assertEqual((target.mode, target.accepting), ("unsupported", False))
        for targets in ([target.id], ["future-node"]):
            with self.assertRaises(RunRequestError):
                await self.session.run.steer(run.id, "not accepted", targets=targets)
        release.set()
        await self.completed()

    async def test_approval_pause_never_executes_tool_before_explicit_resume(self):
        from tests.llm.test_loop import call
        from tests.llm.support.runtime_tools import RuntimeTools
        from llm.components.tools import Tool, ToolRegistry
        from llm.services.runtime.tools import ToolPolicy, ToolApprovalRequired
        from llm.services.configuration import ServiceConfig
        effects = []
        async def act(arguments):
            effects.append(1)
        async def authorize(call):
            raise ToolApprovalRequired("review")
        model = Model([chunk("draft", finish="stop")],
                      [chunk(calls=[call("{}", name="act")], finish="tool_calls")], [chunk("done", finish="stop")])
        component = RuntimeTools(ToolRegistry([Tool("act", "act", {"type": "object"}, act)]))
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app = LargeLanguageModel(folder.name, components=[WorkflowComponent(), AgentComponent(), component],
            services=ServiceConfig(tool_policy=ToolPolicy(authorize=authorize)),
            engines={"graph": GraphEngine("flow", handlers={"agent": AgentNode(engines={"loop": LoopEngine(completion_fn=model)})})})
        self.addAsyncCleanup(self.app.shutdown)
        self.addAsyncCleanup(self.release_model, model)
        self.project = await self.app.projects.acreate("approval", components=["workflows", "agents", "tools"])
        await (await self.project.components.aget("tools")).aenable("act")
        await (await self.project.components.aget("agents")).acreate({"purpose": "act", "engine": "loop",
            "completion": {"model": "test"}, "tools": ["act"]}, identifier="writer")
        await (await self.project.components.aget("workflows")).acreate(agent_graph(), identifier="flow")
        self.session = await self.project.sessions.acreate()
        self.request = await self.session.run.submit("original", engine="graph")
        target, = await self.entered(model)
        await self.session.run.steer(self.run.id, "then act", targets=[target.id])
        model.release.set()
        paused = await self.request.wait(timeout=15)
        self.assertEqual(paused.data.status, "paused", paused.data.error)
        self.assertEqual(effects, [])
        with self.assertRaises(RunRequestError):
            await self.session.run.steer(paused.id, "too late", targets=[target.id])
        interaction, = await paused.ainteractions()
        await paused.arespond(interaction.respond("approve"))
        resumed = await (await self.session.run.resume(paused.id, engine="graph")).wait(timeout=15)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(effects, [1])
        self.assertEqual(len(model.requests), 3)
        self.assertEqual([m.get("content") for m in model.requests[-1]["messages"]].count("then act"), 1)


class CommonSteeringTests(unittest.IsolatedAsyncioTestCase):
    def test_agent_validates_bound_engine_not_only_registered_factory(self):
        class Factory(BaseEngine):
            def for_agent(self, definition):
                return bound
        node = AgentNode(engines={"custom": Factory()})
        for mode, name, resume in (("invalid", "custom", lambda *a: None),
                                   (SteeringMode.CONSUME, " ", lambda *a: None),
                                   (SteeringMode.CONSUME, "custom", None)):
            with self.subTest(mode=mode, name=name, resume=resume):
                bound = BaseEngine()
                bound.steering_mode, bound.checkpoint_name, bound.validate_resume = mode, name, resume
                with self.assertRaises(ValueError):
                    node._engine({"engine": "custom"})

    async def test_standalone_loop_still_accepts_at_run_started_notification(self):
        model = Model([chunk("done", finish="stop")], gate=False)
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, components=[], engines={"loop": LoopEngine(completion_fn=model)}) as app:
                project = await app.projects.acreate("test", components=[], config=ProjectConfig(completion={"model": "test"}))
                session = await project.sessions.acreate()
                accepted = []
                async def on_run(event):
                    if event.type == "started":
                        accepted.append(await session.run.steer(event.run.id, "at start"))
                subscription = app.events.subscribe(on_run, channel="run")
                run = await (await session.run.submit("original", engine="loop")).wait(timeout=10)
                await subscription.aclose()
                self.assertEqual(run.data.status, "completed", run.data.error)
                self.assertEqual(len(accepted), 1)
                self.assertEqual(model.requests[0]["messages"][-1]["content"], "at start")

    async def test_declared_consumer_cannot_silently_omit_channel_lifecycle(self):
        class Broken(BaseEngine):
            steering_mode = SteeringMode.CONSUME
            checkpoint_name = "broken"
            async def run(self, context):
                yield EngineOutput(text="not a valid consumer")
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, components=[], engines={"broken": Broken()}) as app:
                session = await (await app.projects.acreate("test", components=[])).sessions.acreate()
                run = await (await session.run.submit("original", engine="broken")).wait(timeout=10)
                self.assertEqual(run.data.status, "failed")
                self.assertIn("did not open", run.data.error)

    def test_consumer_registration_requires_checkpoint_and_valid_mode(self):
        from llm.engines.registry import EngineRegistry
        class Missing(BaseEngine):
            steering_mode = SteeringMode.CONSUME
        with self.assertRaisesRegex(ValueError, "checkpoint_name"):
            EngineRegistry().register("missing", Missing())
        missing = BaseEngine()
        missing.steering_mode = "automatic"
        with self.assertRaises(ValueError):
            EngineRegistry().register("invalid", missing)

    async def test_custom_engine_uses_non_iteration_boundary_and_ack(self):
        entered, release = asyncio.Event(), asyncio.Event()
        captured = []
        class Consumer(BaseEngine):
            steering_mode = SteeringMode.CONSUME
            checkpoint_name = "custom"

            async def execute(self, context):
                yield EngineEvent(EngineEventType.CHECKPOINT, metadata={"name": "custom", "operation": "initialize",
                    "header": {"input_message_id": context.run.input_message_id,
                               "message_ids": [m.id for m in context.messages]}})
                async with aclosing(self.open_instructions(context)) as events:
                    async for event in events:
                        yield event
                entered.set()
                await release.wait()
                async with aclosing(self.select_instructions(context, checkpoint="custom", boundary="after-reading")) as events:
                    async for event in events:
                        yield event
                captured.extend(m.content for m in context.steering.messages)
                async with aclosing(self.apply_instructions(context, checkpoint="custom", boundary="after-reading")) as events:
                    async for event in events:
                        yield event
                async with aclosing(self.close_instructions(context)) as events:
                    async for event in events:
                        yield event
                yield self.output_event(context, EngineOutput(text="done"))

        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, engines={"custom": Consumer()}, components=[]) as app:
                session = await (await app.projects.acreate("test", components=[])).sessions.acreate()
                request = await session.run.submit("original", engine="custom")
                await asyncio.wait_for(entered.wait(), 10)
                run = await request.aget_run()
                await session.run.steer(run.id, "new input")
                release.set()
                run = await request.wait(timeout=10)
                self.assertEqual(run.data.status, "completed", run.data.error)
                self.assertEqual(captured, ["new input"])
                value, = await run.ainstructions()
                self.assertEqual(value.applications[0]["boundary"], "after-reading")
                self.assertNotIn("iteration", value.applications[0])

    async def test_helper_requires_storage_ack(self):
        from llm.engines.base import SteeringInbox
        from types import SimpleNamespace
        context = SimpleNamespace(steering=SteeringInbox())
        events = BaseEngine.select_instructions(context, checkpoint="custom", boundary="ready")
        await anext(events)
        with self.assertRaisesRegex(RuntimeError, "acknowledgement"):
            await anext(events)

    def test_unrelated_custom_input_checkpoints_are_not_interpreted_as_instructions(self):
        from llm.services.runtime.steering import instruction_records
        checkpoint = {"records": {"read": {"status": "input", "data": {"file": "notes"}},
            "ready": {"kind": "instruction", "status": "input", "target_scope": None, "message_ids": []}}}
        self.assertEqual([key for key, _ in instruction_records(checkpoint)], ["ready"])


class InstructionDataTests(unittest.TestCase):
    def message(self):
        from llm.core.models import Message, MessageRole, MessageStatus
        return Message("instruction", MessageRole.USER, "private content", MessageStatus.QUEUED, run_id="run",
            metadata={"steering": {"run_id": "run", "input_message_id": "original", "status": "pending",
                "targets": [{"id": "root", "scope": None, "status": "pending", "applications": []}]}})

    def test_invalid_message_data_is_rejected_without_conversion(self):
        from llm.core.steering import RunInstruction, InstructionDataError
        changes = [lambda v: v.pop("targets"), lambda v: v.update(targets=[]),
            lambda v: v.update(targets=None), lambda v: v.update(run_id="other"),
            lambda v: v["targets"].append(deepcopy(v["targets"][0])),
            lambda v: v["targets"][0].pop("scope"),
            lambda v: v["targets"][0].update(scope=[]),
            lambda v: v["targets"][0].update(status="done"),
            lambda v: v["targets"][0].update(applications=[{}]),
            lambda v: v.update(status="applied")]
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                message = self.message()
                change(message.metadata["steering"])
                before = deepcopy(message)
                with self.assertRaises(InstructionDataError) as caught:
                    RunInstruction.from_message(message)
                self.assertEqual(caught.exception.code, "invalid_instruction_data")
                self.assertNotIn(message.content, str(caught.exception))
                self.assertEqual(message, before)

    def test_current_pending_and_applied_message_shapes_remain_supported(self):
        from llm.core.steering import RunInstruction
        from llm.core.models import MessageStatus
        message = self.message()
        self.assertEqual(RunInstruction.from_message(message).status, "pending")
        application = {"run_id": "run", "boundary": "ready", "target_id": "root", "step_id": None, "time": message.created_at}
        value = message.metadata["steering"]
        value.update(status="applied", applications=[application])
        value["targets"][0].update(status="applied", applications=[application])
        message.status = MessageStatus.COMMITTED
        self.assertEqual(RunInstruction.from_message(message).status, "applied")

    def test_checkpoint_validation_is_shared_with_loop_resume(self):
        from llm.core.steering import validate_instruction_record, InstructionDataError
        from llm.services.runtime.steering import instruction_records
        valid = {"kind": "instruction", "status": "input", "target_scope": None, "message_ids": ["message"]}
        validate_instruction_record(valid)
        for change in (lambda v: v.pop("target_scope"), lambda v: v.update(message_ids=["x", "x"]),
                       lambda v: v.update(message_ids=[{}]), lambda v: v.update(status="completed")):
            value = deepcopy(valid)
            change(value)
            with self.assertRaises(InstructionDataError):
                list(instruction_records({"records": {"ready": value}}))
        # Loop만 자신의 local key를 안다. 공통 서비스에 옛 형식/엔진별 키 탐지를 넣지 않는다.
        for value in ({"status": "input", "message_ids": []}, {**valid, "target_scope": []}):
            with self.assertRaises(InstructionDataError):
                LoopEngine().validate_resume({"header": {"format": "loop-iterations-v1"},
                                              "records": {"steering:1": value}})

    def test_invalid_target_descriptors_have_a_stable_error(self):
        from llm.core.steering import SteeringTarget, InstructionDataError
        valid = {"id": "root", "run_id": "run", "engine": "loop", "mode": "consume", "accepting": True}
        for update in ({"id": ""}, {"mode": "bad"}, {"scope": []}, {"accepting": "yes"},
                       {"mode": "forward"}, {"reason": "completed"}):
            with self.subTest(update=update), self.assertRaises(InstructionDataError):
                SteeringTarget.from_dict({**valid, **update})
