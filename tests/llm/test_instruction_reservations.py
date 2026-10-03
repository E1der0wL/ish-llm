"""Graph의 미래 Agent 실행 예약. 실제 서비스/저장/체크포인트와 제어 가능한 모델을 사용한다."""

import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from llm.llm import SteeringRoute, EngineEventType, LargeLanguageModel
from llm.components.agents import AgentComponent
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from llm.engines.loop import LoopEngine
from tests.llm import test_engine_steering as fixtures
from tests.llm.test_loop_steering import Model
from tests.llm.test_loop import chunk


class ReservationTests(unittest.IsolatedAsyncioTestCase):
    setup_graph = fixtures.GraphSteeringTests.setup_graph
    release_model = fixtures.GraphSteeringTests.release_model
    entered = fixtures.GraphSteeringTests.entered
    completed = fixtures.GraphSteeringTests.completed

    async def gated(self, models, graph=None, *, graphs=None, handlers=None, profiles=None):
        self.gate_entered, self.gate_release = asyncio.Event(), asyncio.Event()
        async def gate(node):
            self.gate_entered.set()
            await self.gate_release.wait()
            return {}
        document = deepcopy(graph or fixtures.agent_graph())
        document["nodes"]["gate"] = {"type": "gate"}
        document["edges"].append({"source": "gate", "target": document["entry"]})
        document["entry"] = "gate"
        nodes = handlers or {"agent": AgentNode(engines={
            name: LoopEngine(completion_fn=model) for name, model in models.items()})}
        await self.setup_graph(models, document, graphs=graphs, profiles=profiles, handlers={**nodes, "gate": gate})
        async def release():
            self.gate_release.set()
        self.addAsyncCleanup(release)
        await asyncio.wait_for(self.gate_entered.wait(), 10)
        self.run = await self.request.aget_run()
        return await self.run.ainstruction_routes()

    @staticmethod
    def contents(model, index=0):
        return [m.get("content") for m in model.requests[index]["messages"]]

    async def reserve(self, routes, content="one shot"):
        return await self.session.run.reserve_instruction(self.run.id, content, targets=routes)

    async def test_repeat_consumes_once_and_routes_are_persisted_before_notification(self):
        model = Model([chunk("first", finish="stop")], [chunk("second", finish="stop")], gate=False)
        graph = (WorkflowGraph(entry="repeat").node("repeat", "loop", body=fixtures.agent_graph(),
                 max_iterations=2, on_limit="continue").node("end", "end").connect("repeat", "end").to_dict())
        routes = await self.gated({"writer": model}, graph)
        self.assertEqual(routes, [SteeringRoute(("flow", "repeat"), "work", "writer")])
        self.assertEqual(SteeringRoute.from_dict(routes[0].to_dict()), routes[0])
        event, = [e for e in self.events if e.type == EngineEventType.STEERING_CHANGED and "routes" in e.metadata]
        self.assertEqual(event.metadata["routes"], [r.to_dict() for r in routes])
        value = await self.reserve(routes)
        self.assertIsNone(value.targets[0]["scope"])
        self.gate_release.set()
        run = await self.completed()
        self.assertEqual(self.contents(model).count("one shot"), 1)
        self.assertNotIn("one shot", self.contents(model, 1))
        value, = await run.ainstructions()
        self.assertEqual((value.status, len(value.applications)), ("applied", 1))
        self.assertEqual(value.targets[0]["scope"], '["repeat",1,"work"]')

    async def test_multitarget_parallel_has_independent_consumption_and_single_body(self):
        models = {name: Model([chunk(name, finish="stop")], gate=False) for name in ("a", "b")}
        routes = await self.gated(models, fixtures.parallel_graph())
        await self.reserve(routes)
        self.assertEqual((await self.session.run.astatus()).queued_count, 0)
        self.gate_release.set()
        run = await self.completed()
        value, = await run.ainstructions()
        self.assertEqual((value.status, len(value.applications)), ("applied", 2))
        self.assertEqual(len({t["scope"] for t in value.targets}), 2)
        self.assertTrue(all(self.contents(model).count("one shot") == 1 for model in models.values()))
        self.assertEqual(sum(m.content == "one shot" for m in await self.session.aconversation()), 1)

    async def test_skipped_branch_is_unapplied_with_reason_and_partial_status(self):
        graph = (WorkflowGraph(entry="choose").node("choose", "branch", cases=[
            {"when": {"path": "/pick", "op": "eq", "value": "a"}, "port": "a"}], default="b")
            .node("a", "agent", agent="a").node("b", "agent", agent="b").node("end", "end")
            .connect("choose", "a", port="a").connect("choose", "b", port="b")
            .connect("a", "end").connect("b", "end").to_dict())
        graph["initial_state"] = {"pick": "a"}
        a, b = Model([chunk("a", finish="stop")], gate=False), Model(gate=False)
        routes = await self.gated({"a": a, "b": b}, graph)
        await self.reserve(routes)
        self.gate_release.set()
        value, = await (await self.completed()).ainstructions()
        self.assertEqual(value.status, "partially_applied")
        skipped = next(t for t in value.targets if t["reservation"]["node_id"] == "b")
        self.assertEqual((skipped["status"], skipped["reason"]), ("unapplied", "node_not_reached"))
        self.assertEqual(b.requests, [])

    async def test_same_nested_workflow_at_distinct_call_sites_does_not_broadcast(self):
        model = Model([chunk("left", finish="stop")], [chunk("right", finish="stop")], gate=False)
        graph = (WorkflowGraph(entry="left/odd").node("left/odd", "workflow", workflow="nested")
            .node("right", "workflow", workflow="nested").node("end", "end")
            .connect("left/odd", "right").connect("right", "end").to_dict())
        routes = await self.gated({"writer": model}, graph, graphs={"nested": fixtures.agent_graph()})
        self.assertEqual(len(routes), 2)
        await self.reserve([r for r in routes if "right" in r.workflow_path])
        self.gate_release.set()
        await self.completed()
        self.assertNotIn("one shot", self.contents(model))
        self.assertEqual(self.contents(model, 1).count("one shot"), 1)

    async def test_nested_graph_agent_exposes_only_leaf_reservations(self):
        model = Model([chunk("leaf", finish="stop")], gate=False)
        leaf = AgentNode(engines={"writer": LoopEngine(completion_fn=model)})
        outer = AgentNode(engines={"subgraph": GraphEngine("nested", handlers={"agent": leaf})})
        profiles = {"writer": {"purpose": "write", "engine": "writer", "completion": {"model": "test"}},
            "coordinator": {"purpose": "delegate", "engine": "subgraph", "workflow": "nested"}}
        routes = await self.gated({"writer": model}, fixtures.agent_graph("coordinator"),
            graphs={"nested": fixtures.agent_graph()}, profiles=profiles, handlers={"agent": outer})
        self.assertEqual(routes, [SteeringRoute(("flow", "work", "workflow", "nested"), "work", "writer")])
        await self.reserve(routes)
        self.gate_release.set()
        await self.completed()
        self.assertIn("one shot", self.contents(model))

    async def test_reservation_during_execution_goes_to_next_invocation_only(self):
        model = Model([chunk("first", finish="stop")], [chunk("second", finish="stop")])
        graph = (WorkflowGraph(entry="repeat").node("repeat", "loop", body=fixtures.agent_graph(),
                 max_iterations=2, on_limit="continue").node("end", "end").connect("repeat", "end").to_dict())
        await self.setup_graph({"writer": model}, graph)
        await self.entered(model)
        await self.reserve(await self.run.ainstruction_routes())
        model.release.set()
        await self.completed()
        self.assertNotIn("one shot", self.contents(model))
        self.assertEqual(self.contents(model, 1).count("one shot"), 1)

    async def test_no_next_invocation_does_not_redirect_to_current_agent(self):
        model = Model([chunk("done", finish="stop")])
        await self.setup_graph({"writer": model})
        await self.entered(model)
        await self.reserve(await self.run.ainstruction_routes())
        model.release.set()
        value, = await (await self.completed()).ainstructions()
        self.assertEqual((value.status, value.reason), ("unapplied", "node_not_reached"))
        self.assertEqual(len(model.requests), 1)

    async def test_partial_multitarget_resume_filters_each_receipt_not_message_aggregate(self):
        a = Model([chunk("a", finish="stop")], gate=False)
        b = Model([chunk("b", finish="stop")], gate=False)
        graph = (WorkflowGraph(entry="a").node("a", "agent", agent="a").node("b", "agent", agent="b")
                 .node("end", "end").connect("a", "b").connect("b", "end").to_dict())
        routes = await self.gated({"a": a, "b": b}, graph)
        await self.reserve(routes)
        from llm.services.runtime import steering
        apply = steering.apply_instructions
        def fail_b(*args, **kwargs):
            if kwargs["target"]["scope"] == '["b"]':
                raise OSError("b input not committed")
            return apply(*args, **kwargs)
        with patch.object(steering, "apply_instructions", side_effect=fail_b):
            self.gate_release.set()
            failed = await self.request.wait(timeout=15)
        before, = await failed.ainstructions()
        self.assertEqual((failed.data.status, before.status), ("failed", "partially_applied"))
        self.assertEqual(b.requests, [])
        resumed = await (await self.session.run.resume(failed.id, engine="graph")).wait(timeout=15)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(len(a.requests), 1)
        self.assertNotIn("one shot", self.contents(b))
        self.assertEqual((await resumed.ainstructions())[0], before)

    def test_duplicate_execution_scope_is_rejected_before_persistence(self):
        from types import SimpleNamespace
        from llm.core.steering import SteeringTarget, SteeringMode
        from llm.services.runtime.steering import steering_boundary
        from llm.engines.base import EngineEvent
        target = SteeringTarget("a", "run", "loop", SteeringMode.CONSUME, scope='["work"]', accepting=True)
        run = SimpleNamespace(id="run", metadata={"steering": {
            "targets": {"a": target.to_dict()}, "root_opened": False, "accepting": True}})
        event = EngineEvent(EngineEventType.STEERING, metadata={"operation": "open", "target_id": "b",
            "target": {**target.to_dict(), "id": "b"}})
        before = deepcopy(run.metadata)
        with self.assertRaises(ValueError):
            steering_boundary(None, run, None, event)
        self.assertEqual(run.metadata, before)

    async def test_invalid_route_or_duplicate_is_atomic_rejection(self):
        routes = await self.gated({"writer": Model([chunk("done", finish="stop")], gate=False)})
        for targets in ([], [*routes, *routes], [*routes, SteeringRoute(("flow",), "gate", "writer")],
                        [SteeringRoute(("flow",), "work", "wrong")], [routes[0].to_dict()]):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                await self.reserve(targets)
        self.assertEqual(await self.run.ainstructions(), [])
        self.gate_release.set()
        await self.completed()

    async def test_pause_before_target_does_not_transfer_unused_reservation(self):
        model = Model([chunk("done", finish="stop")], gate=False)
        graph = fixtures.agent_graph()
        graph["nodes"]["work"]["pause_before"] = True
        routes = await self.gated({"writer": model}, graph)
        await self.reserve(routes)
        self.gate_release.set()
        paused = await self.request.wait(timeout=15)
        self.assertEqual(paused.data.status, "paused", paused.data.error)
        value, = await paused.ainstructions()
        self.assertEqual((value.status, value.reason), ("unapplied", "paused"))
        interaction, = await paused.ainteractions()
        await paused.arespond(interaction.respond("approve"))
        resumed = await (await self.session.run.resume(paused.id, engine="graph")).wait(timeout=15)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertNotIn("one shot", self.contents(model))
        self.assertEqual(await resumed.ainstructions(), [])

    async def test_selected_but_unapplied_reservation_is_not_restored_on_resume(self):
        model = Model([chunk("done", finish="stop")], gate=False)
        routes = await self.gated({"writer": model})
        instruction = await self.reserve(routes)
        from llm.services.runtime import steering
        def fail(*args, **kwargs):
            raise OSError("apply persistence failed")
        with patch.object(steering, "apply_instructions", side_effect=fail):
            self.gate_release.set()
            failed = await self.request.wait(timeout=15)
        self.assertEqual(failed.data.status, "failed")
        self.assertEqual(model.requests, [])
        before = await failed.acheckpoint()
        self.assertTrue(any(r.get("payload", {}).get("message_ids") == [instruction.id] for r in before["records"].values()))
        value, = await failed.ainstructions()
        self.assertEqual(value.status, "unapplied")
        resumed = await (await self.session.run.resume(failed.id, engine="graph")).wait(timeout=15)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertNotIn("one shot", self.contents(model))
        self.assertEqual(await resumed.ainstructions(), [])
        self.assertEqual(await failed.acheckpoint(), before)

    async def test_applied_reservation_restores_context_once_without_new_consumption_after_reopen(self):
        model = Model([ConnectionError("offline")], [chunk("done", finish="stop")], gate=False)
        routes = await self.gated({"writer": model})
        await self.reserve(routes)
        self.gate_release.set()
        failed = await self.request.wait(timeout=15)
        self.assertEqual(failed.data.status, "failed")
        before, = await failed.ainstructions()
        ids = self.project.id, self.session.id
        await self.app.shutdown()
        reopened = LargeLanguageModel(self.root, engines={"graph": self.engine}, components=[WorkflowComponent(), AgentComponent()])
        self.addAsyncCleanup(reopened.shutdown)
        session = await (await reopened.projects.aload(ids[0])).sessions.aload(ids[1])
        resumed = await (await session.run.resume(failed.id, engine="graph")).wait(timeout=15)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(self.contents(model, 1).count("one shot"), 1)
        after, = await resumed.ainstructions()
        self.assertEqual(after, before)
        self.assertEqual(len(after.applications), 1)

    async def test_interrupt_cancels_unbound_reservations_and_fresh_run_does_not_inherit(self):
        model = Model([chunk("fresh", finish="stop")], gate=False)
        routes = await self.gated({"writer": model})
        await self.reserve(routes)
        await self.session.run.interrupt()
        stopped = await self.request.wait(timeout=15)
        value, = await stopped.ainstructions()
        self.assertEqual(value.status, "unapplied")
        self.assertTrue(value.reason)
        self.gate_release.set()
        fresh = await (await self.session.run.submit("restart", engine="graph")).wait(timeout=15)
        self.assertEqual(fresh.data.status, "completed", fresh.data.error)
        self.assertNotIn("one shot", self.contents(model))
        self.assertEqual(await fresh.ainstructions(), [])

    async def test_binding_storage_failure_rolls_back_and_prevents_agent_execution(self):
        model = Model(gate=False)
        routes = await self.gated({"writer": model})
        await self.reserve(routes)
        save = self.app.run_repository.save
        def fail(run):
            save(run)
            if run.metadata.get("steering", {}).get("bindings"):
                raise OSError("binding commit failure")
        with patch.object(self.app.run_repository, "save", side_effect=fail):
            self.gate_release.set()
            run = await self.request.wait(timeout=15)
        self.assertEqual(run.data.status, "failed")
        self.assertEqual(model.requests, [])
        value, = await run.ainstructions()
        self.assertEqual((value.status, value.targets[0]["scope"]), ("unapplied", None))

    async def test_admission_after_binding_before_agent_open_waits_for_next_execution(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class GatedAgent(AgentNode):
            async def __call__(self, node):
                entered.set()
                await release.wait()
                return await super().__call__(node)
        model = Model([chunk("done", finish="stop")], gate=False)
        handler = GatedAgent(engines={"writer": LoopEngine(completion_fn=model)})
        await self.setup_graph({"writer": model}, handlers={"agent": handler})
        async def cleanup():
            release.set()
        self.addAsyncCleanup(cleanup)
        await asyncio.wait_for(entered.wait(), 10)
        self.run = await self.request.aget_run()
        self.assertEqual(await self.run.ainstruction_targets(), [])
        await self.reserve(await self.run.ainstruction_routes())
        release.set()
        value, = await (await self.completed()).ainstructions()
        self.assertEqual((value.status, value.reason), ("unapplied", "node_not_reached"))
        self.assertNotIn("one shot", self.contents(model))

    async def test_unsupported_agent_has_no_reservation_route(self):
        from llm.engines.base import BaseEngine
        from llm.core.results import EngineOutput
        class Plain(BaseEngine):
            def for_agent(self, definition):
                return self
            async def run(self, context):
                yield EngineOutput(text="plain")
        routes = await self.gated({}, handlers={"agent": AgentNode(engines={"plain": Plain()})},
            profiles={"writer": {"engine": "plain", "purpose": "plain"}})
        self.assertEqual(routes, [])
        with self.assertRaises(ValueError):
            await self.reserve([SteeringRoute(("flow",), "work", "plain")])
        self.gate_release.set()
        await self.completed()

    async def test_current_run_routes_use_snapshot_even_if_workflow_is_edited(self):
        model = Model([chunk("old definition", finish="stop")], gate=False)
        routes = await self.gated({"writer": model})
        workflows = await self.project.components.aget("workflows")
        changed = (WorkflowGraph(entry="new").node("new", "agent", agent="writer")
            .node("end", "end").connect("new", "end").to_dict())
        await workflows.aupdate("flow", changed)
        self.assertEqual(await self.run.ainstruction_routes(), routes)
        await self.reserve(routes)
        self.gate_release.set()
        await self.completed()
        self.assertIn("one shot", self.contents(model))

    async def test_reservation_validation_rejects_corrupt_receipts_without_mutation(self):
        from llm.core.steering import RunInstruction, InstructionDataError
        routes = await self.gated({"writer": Model([chunk("done", finish="stop")], gate=False)})
        instruction = await self.reserve(routes)
        message = next(m for m in await self.session.aconversation() if m.id == instruction.id)
        for change in (lambda t: t.update(reservation=None), lambda t: t.pop("execution_id"),
                       lambda t: t.update(execution_id="unbound"),
                       lambda t: t["reservation"].update(workflow_path=[]),
                       lambda t: t.update(applications=[{}])):
            broken = deepcopy(message)
            change(broken.metadata["steering"]["targets"][0])
            before = deepcopy(broken)
            with self.assertRaises(InstructionDataError):
                RunInstruction.validate_message(broken)
            self.assertEqual(broken, before)
        broken = deepcopy(message)
        target = broken.metadata["steering"]["targets"][0]
        target["scope"] = '["work"]'
        other = deepcopy(target)
        other.update(id="other-receipt")
        other["reservation"]["node_id"] = "other-node"
        broken.metadata["steering"]["targets"].append(other)
        with self.assertRaisesRegex(InstructionDataError, "duplicate bound"):
            RunInstruction.validate_message(broken)
        self.gate_release.set()
        await self.completed()

    async def test_stale_run_recovery_finishes_reservation_without_scheduling_it(self):
        from llm.core.models import RunStatus, MessageRole, MessageStatus
        from llm.services.runtime.steering import reservation_receipts
        model = Model([chunk("done", finish="stop")], gate=False)
        routes = await self.gated({"writer": model})
        self.gate_release.set()
        run = await self.completed()
        await self.session.run.shutdown()
        def stale():
            record = self.app.run_repository.load(self.session.data, run.id)
            record.status, record.ended_at = RunStatus.RUNNING, None
            record.metadata["steering"]["accepting"] = True
            self.app.run_repository.save(record)
            store = self.app.project_manager.sessions.conversations(self.session.data)
            store.set_status(record.assistant_message_id, MessageStatus.STREAMING)
            return store.create(MessageRole.USER, "pending before crash", MessageStatus.QUEUED, run_id=run.id,
                metadata={"steering": {"run_id": run.id, "input_message_id": record.input_message_id,
                    "status": "pending", "targets": reservation_receipts(record, routes)}})
        message = await self.app._storage_call(stale)
        await self.session.run.start()
        await asyncio.wait_for(self.session.run.wait_idle(), 10)
        self.assertEqual(len(await self.session.run.alist()), 1)
        self.assertEqual((await run.aget_data()).status, "interrupted")
        value, = await run.ainstructions()
        self.assertEqual((value.id, value.status, value.reason), (message.id, "unapplied", "process_restart"))
        self.assertEqual(len(model.requests), 1)
