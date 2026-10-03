"""Graph의 순수 판단과 실제 이벤트/저장 경계가 같은 실행 계약을 유지하는지 검증한다."""

from copy import deepcopy
import tempfile
import unittest

from llm.components.workflows import WorkflowGraph
from llm.core.models import RunStatus, StepStatus
from llm.engines.base import EngineEventType
from llm.engines.graph import GraphEngine
from llm.engines.graph.engine import GraphExecutionError, _branch_port, _loop_continues, _plan_node
from llm.llm import LargeLanguageModel


class DecisionTests(unittest.TestCase):
    def test_branch_priority_default_and_json_types_without_runtime(self):
        node = {"cases": [
            {"port": "number", "when": {"path": "/value", "op": "eq", "value": 1}},
            {"port": "present", "when": {"path": "/value", "op": "exists"}},
        ], "default": "missing"}
        for state, expected in (({"value": 1}, "number"), ({"value": True}, "present"), ({}, "missing")):
            with self.subTest(state=state):
                before = deepcopy((node, state))
                self.assertEqual(_branch_port(node, state), expected)
                self.assertEqual((node, state), before)

    def test_branch_stops_after_first_match(self):
        node = {"cases": [
            {"port": "first", "when": {"path": "/value", "op": "exists"}},
            {"port": "incomparable", "when": {"path": "/value", "op": "gt", "value": 1}},
        ], "default": "missing"}
        self.assertEqual(_branch_port(node, {"value": True}), "first")

    def test_fixed_loop_finishes_at_limit_even_with_fail_policy(self):
        node = {"max_iterations": 2, "on_limit": "fail"}
        self.assertTrue(_loop_continues(node, {}, 0))
        self.assertTrue(_loop_continues(node, {}, 1))
        self.assertFalse(_loop_continues(node, {}, 2))

    def test_conditional_loop_stops_on_success_and_distinguishes_limit_policy(self):
        node = {"max_iterations": 2, "on_limit": "fail",
                "while": {"path": "/passed", "op": "eq", "value": False}}
        before = deepcopy(node)
        self.assertTrue(_loop_continues(node, {"passed": False}, 1))
        self.assertFalse(_loop_continues(node, {"passed": True}, 2))
        self.assertFalse(_loop_continues(node, {}, 2))
        with self.assertRaisesRegex(GraphExecutionError, "iteration limit"):
            _loop_continues(node, {"passed": False}, 2)
        self.assertFalse(_loop_continues({**node, "on_limit": "continue"}, {"passed": False}, 2))
        self.assertEqual(node, before)

    def test_node_action_table_and_inputs_are_unchanged(self):
        state = {"value": [1]}
        for status in (None, "waiting", "started", "completed"):
            for resuming in (False, True):
                for pause in (False, True):
                    with self.subTest(status=status, resuming=resuming, pause=pause):
                        node = {"type": "work", "pause_before": pause}
                        saved = None if status is None else {"status": status, "input_state": deepcopy(state)}
                        before = deepcopy((node, state, saved))
                        plan = _plan_node(node, state, saved=saved, resuming=resuming, decisions={}, key="key")
                        expected = "reuse" if status == "completed" else (
                            "pause" if pause and not (resuming and saved) else "execute")
                        self.assertEqual(plan.action, expected)
                        self.assertEqual((node, state, saved), before)

    def test_completed_and_container_inputs_cannot_change(self):
        for status, container in (("completed", False), ("started", True), ("waiting", True)):
            with self.subTest(status=status):
                saved = {"status": status, "container": container, "input_state": {"x": 1}}
                with self.assertRaisesRegex(GraphExecutionError, "input"):
                    _plan_node({"pause_before": True}, {"x": 2}, saved=saved,
                               resuming=True, decisions={}, key="key")

    def test_explicit_decision_overrides_saved_decision_without_mutating_it(self):
        saved = {"status": "started", "decision": {"approved": False, "state": {"x": [1]}}}
        decisions = {"key": {"approved": True, "state": {"x": [2]}}}
        before = deepcopy((saved, decisions))
        plan = _plan_node({}, {}, saved=saved, resuming=True, decisions=decisions, key="key")
        self.assertEqual(plan.action, "execute")
        self.assertEqual(plan.decision, decisions["key"])
        plan.decision["state"]["x"].append(3)
        self.assertEqual((saved, decisions), before)
        # 빈 명시 응답도 과거 응답을 대체한다. 누락된 응답과 혼동하지 않는다.
        self.assertEqual(_plan_node({}, {}, saved=saved, resuming=True,
                                   decisions={"key": {}}, key="key").decision, {})

    def test_rejected_continuation_fails_but_saved_accepted_decision_is_reused(self):
        for decisions, saved in (({"key": {"approved": False}}, None),
                                 ({}, {"status": "started", "decision": {"approved": False}})):
            with self.assertRaisesRegex(GraphExecutionError, "rejected"):
                _plan_node({}, {}, saved=saved, resuming=True, decisions=decisions, key="key")
        saved = {"status": "started", "decision": {"approved": True, "state": {"value": [1]}}}
        plan = _plan_node({}, {}, saved=saved, resuming=True, decisions={}, key="key")
        self.assertEqual(plan.decision, saved["decision"])
        plan.decision["state"]["value"].append(2)
        self.assertEqual(saved["decision"]["state"]["value"], [1])


class DecisionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def setup_graph(self, definition, work, *, on_event=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = LargeLanguageModel(temporary.name,
            engines={"graph": GraphEngine(handlers={"work": work})}, on_event=on_event)
        self.addAsyncCleanup(self.app.shutdown)
        project = await self.app.projects.acreate(components=["workflows"])
        await project.components.workflows.acreate(definition, identifier="flow")
        self.session = await project.sessions.acreate()

    async def test_pause_resume_plan_keeps_checkpoint_step_and_effect_order(self):
        trace, names, calls = [], {}, []
        def observe(run, event):
            if event.type == EngineEventType.CHECKPOINT and event.metadata.get("operation") == "record":
                value = event.metadata["value"]
                if value["node_id"] in ("first", "review"):
                    trace.append(("record", value["node_id"], value["status"]))
            elif event.type == EngineEventType.STEP_STARTED and event.kind == "graph_node":
                names[event.step_id] = event.name
                if event.name in ("first", "review"):
                    trace.append(("start", event.name))
            elif event.type == EngineEventType.STEP_COMPLETED and names.get(event.step_id) in ("first", "review"):
                trace.append(("complete", names[event.step_id]))
        async def work(node):
            calls.append(node.node_id)
            if node.node_id in ("first", "review"):
                trace.append(("effect", node.node_id))
            saved = self.app.run_repository.checkpoint(node.context.run)["records"][node.checkpoint_key]
            self.assertEqual(saved["status"], "started")
            self.assertTrue(any(step.name == node.node_id and step.status == StepStatus.RUNNING
                                for step in self.app.step_manager.list(node.context.run)))
            return {}
        graph = (WorkflowGraph(entry="first")
            .node("first", "work").node("review", "work", pause_before=True, resume_schema={
                "type": "object", "properties": {"choice": {"type": "string"}}, "additionalProperties": False})
            .node("route", "branch", cases=[{"port": "yes", "when": {
                "path": "/choice", "op": "eq", "value": "yes"}}], default="no")
            .node("yes", "work").node("no", "work").node("end", "end")
            .connect("first", "review").connect("review", "route")
            .connect("route", "yes", port="yes").connect("route", "no", port="no")
            .connect("yes", "end").connect("no", "end").to_dict())
        await self.setup_graph(graph, work, on_event=observe)
        paused = await (await self.session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait(timeout=15)
        self.assertEqual(paused.data.status, RunStatus.PAUSED)
        checkpoint = await paused.acheckpoint()
        self.assertEqual(trace, [("record", "first", "started"), ("start", "first"), ("effect", "first"),
            ("record", "first", "completed"), ("complete", "first"), ("record", "review", "waiting")])
        trace.clear()
        resumed = await (await self.session.run.resume(paused.id, engine="graph",
            decisions={'["review"]': {"state": {"choice": "yes"}}})).wait(timeout=15)
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(calls, ["first", "review", "yes"])
        self.assertEqual(trace, [("start", "first"), ("complete", "first"),
            ("record", "review", "started"), ("start", "review"), ("effect", "review"),
            ("record", "review", "completed"), ("complete", "review")])
        self.assertEqual(await paused.acheckpoint(), checkpoint)

    async def test_invalid_branch_comparison_still_fails_started_step_before_action(self):
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {}
        graph = (WorkflowGraph(entry="route", initial_state={"value": True})
            .node("route", "branch", cases=[{"port": "yes", "when": {
                "path": "/value", "op": "gt", "value": 1}}], default="no")
            .node("action", "work").node("end", "end")
            .connect("route", "action", port="yes").connect("route", "end", port="no")
            .connect("action", "end").to_dict())
        await self.setup_graph(graph, work)
        failed = await (await self.session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait(timeout=15)
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertIn("Ordered comparison", failed.data.error)
        self.assertEqual(calls, [])
        steps = await failed.steps.alist()
        self.assertEqual({step.kind for step in steps}, {"graph", "graph_node"})
        self.assertTrue(all(step.status == StepStatus.FAILED for step in steps))
        self.assertEqual((await failed.acheckpoint())["records"]['["route"]']["status"], "started")
