"""중첩 Workflow/Graph Agent의 저장, 재개, 실행 한도와 LangGraph 스케줄링 통합 검사."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
import json
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

from tests.llm.support.runtime_tools import RuntimeTools
from llm.core.agents import AgentDefinition
from llm.components.skills import SkillComponent
from llm.components.tools import Tool, ToolRegistry
from llm.components.tools.component import ToolComponent
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.models import RunStatus, StepStatus
from llm.engines.graph.agent import AgentNode
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine
from llm.engines.graph.tool import ToolNode
from llm.llm import LargeLanguageModel
from llm.services.runtime.runs import RunRequestError
from llm.services.composition import BackendServices
from llm.services.runtime.tools import ToolRuntime
from tests.llm.test_loop import ScriptedCompletion, chunk
from tests.llm.support.runtime_tools import TEST_CONTRACT


def action_graph(kind="work", **options):
    return (WorkflowGraph(entry="work").node("work", kind, **options)
            .node("end", "end").connect("work", "end").to_dict())


class NestedGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_loop_resume_scopes_partial_output_and_reuses_effects(self):
        from tests.llm.test_loop import call
        model = ScriptedCompletion(
            [chunk(calls=[call('{"value":"once"}', name='effect')], finish='tool_calls')],
            [chunk('nested unfinished text'), ConnectionError('offline')],
            [chunk('done', finish='stop')])
        handler = AgentNode(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        await self.setup({'main': action_graph('agent', agent='worker')}, handlers={'agent': handler})
        await self.agents.acreate({'purpose': 'work', 'engine': 'loop', 'completion': {'model': 'test'},
                                  'tools': ['effect']}, identifier='worker')
        failed = await self.request()
        self.assertEqual(failed.data.status, RunStatus.FAILED, failed.data.error)
        done = await (await self.session.run.resume(failed.id, agent_id='main-agent', partial_output=True)).wait()
        self.assertEqual(done.data.status, RunStatus.COMPLETED, done.data.error)
        self.assertEqual(self.effects, [{'value': 'once'}])
        self.assertIn('nested unfinished text', str(model.requests[-1]['messages']))

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.effects = []
        async def effect(arguments):
            self.effects.append(arguments)
            return {"value": arguments.get("value", "done")}
        self.tools = ToolRegistry((Tool("effect", "record", {"type": "object"}, effect, contract=TEST_CONTRACT),))

    async def setup(self, graphs, *, handlers=None, services=None, policies=None, **options):
        async def work(node):
            self.effects.append(node.inputs)
            return {"answer": node.inputs.get("request", "done")}
        self.engine = GraphEngine(handlers=handlers or {"work": work})
        # Agent writers validate selected factories, including child environments.
        engines, visited = {"graph": self.engine}, set()
        def register_selected(engine):
            if id(engine) in visited:
                return
            visited.add(id(engine))
            for handler in getattr(engine, "handlers", {}).values():
                if isinstance(handler, AgentNode):
                    for name in handler.engines.names():
                        selected = handler.engines.get(name)
                        engines.setdefault(name, selected)
                        register_selected(selected)
        register_selected(self.engine)
        self.app = LargeLanguageModel(self.root, engines=engines, services=services, components=[
            WorkflowComponent(), RuntimeTools(self.tools), SkillComponent()])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("nested", components=['workflows', 'tools', 'skills'], config={
            "policies": policies or {}, "parameters": {"engines": {"graph": GraphEngine.parameter_layout.pack(options)}}})
        self.workflows = await self.project.components.aget("workflows")
        for name, graph in graphs.items():
            await self.workflows.acreate(graph, identifier=name)
        self.agents = self.project.agents
        await self.agents.acreate({"engine": "graph", "engine_options": {"workflow": "main"}}, identifier="main-agent")
        await (await self.project.components.aget("tools")).aenable("effect")
        self.session = await self.project.sessions.acreate()

    async def request(self):
        return await (await self.session.run.submit("hello", agent_id="main-agent")).wait(timeout=25)

    async def output(self, run):
        return next(s.output.data for s in await run.steps.alist() if s.kind == "graph")

    async def test_loop_agent_approval_reopen_preserves_tool_receipts_and_budget(self):
        from llm.services.runtime.tools import ToolApprovalRequired
        from tests.llm.test_loop import call
        approvals = []
        async def authorize(call):
            approvals.append(call.name)
            if len(approvals) == 2:
                raise ToolApprovalRequired("review second effect")
            return True
        model = ScriptedCompletion(
            [chunk(calls=[call('{"value":1}', name='effect', call_id='first')], finish='tool_calls')],
            [chunk(calls=[call('{"value":2}', name='effect', call_id='second')], finish='tool_calls')],
            [chunk('done', finish='stop')])
        handler = AgentNode(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        await self.setup({'main': action_graph('agent', agent='worker')}, handlers={'agent': handler},
                         services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize)))
        await self.agents.acreate({'purpose':'work', 'engine':'loop', 'completion':{'model':'test'},
            'tools':['effect'], 'policy':{'max_tool_calls':2, 'require_tool':True}}, identifier='worker')
        paused = await self.request()
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        self.assertEqual(self.effects, [{'value':1}])
        checkpoint = await paused.acheckpoint()
        keys = [k for k,v in checkpoint['records'].items() if v.get('approval_required')]
        self.assertEqual(len(keys), 1)
        project_id, session_id, run_id = self.project.id, self.session.id, paused.id
        await self.app.shutdown()
        reopened = LargeLanguageModel(self.root, engines={'graph': self.engine}, components=[
            WorkflowComponent(), RuntimeTools(self.tools), SkillComponent()],
            services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize)))
        self.addAsyncCleanup(reopened.shutdown)
        session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
        paused = await session.run.aload(run_id)
        interaction, = await paused.ainteractions()
        self.assertEqual(interaction.binding['key'], keys[0])
        await paused.arespond(interaction.respond('approve'))
        resumed = await (await session.run.resume(run_id, agent_id='main-agent')).wait()
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(self.effects, [{'value':1},{'value':2}])
        self.assertEqual(len(model.requests), 3)
        self.assertTrue(any(s.metadata.get('reused') for s in await resumed.steps.alist()))

    async def test_resume_preserves_one_call_agent_budget_and_blocks_new_effect(self):
        """R3: restoring usage is insufficient if Agent max_tool_calls is dropped."""
        await self._resume_agent_budget()

    async def test_missing_agent_usage_rejects_before_reopened_resume_effect(self):
        await self._resume_agent_budget(damage="envelope")

    async def test_missing_agent_usage_counter_rejects_before_reopened_resume_effect(self):
        await self._resume_agent_budget(damage="counter")

    async def test_missing_latest_agent_usage_does_not_restore_older_usage(self):
        await self._resume_agent_budget(damage="latest")

    async def _resume_agent_budget(self, *, damage=None):
        from tests.llm.test_loop import call
        effect_file = self.root / "budget-effects.txt"
        async def effect(arguments):
            with effect_file.open("a", encoding="utf-8") as output:
                output.write(arguments["value"] + "\n")
            return {"value": arguments["value"]}
        self.tools = ToolRegistry((Tool("effect", "record", {"type": "object"}, effect, contract=TEST_CONTRACT),))
        model = ScriptedCompletion(
            [chunk(calls=[call('{"value":"first"}', name="effect", call_id="first")], finish="tool_calls")],
            [ConnectionError("pause after first completed effect")],
            [chunk(calls=[call('{"value":"second"}', name="effect", call_id="second")], finish="tool_calls")],
            [chunk("done", finish="stop")])
        handler = AgentNode(engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        # No parent Tool cap: it must not mask a missing Agent limit.
        await self.setup({"main": action_graph("agent", agent="worker")}, handlers={"agent": handler})
        await self.agents.acreate({"purpose": "work", "engine": "loop", "completion": {"model": "test"},
            "tools": ["effect"], "policy": {"max_tool_calls": 1}}, identifier="worker")
        failed = await self.request()
        self.assertEqual(failed.data.status, RunStatus.FAILED, failed.data.error)
        self.assertEqual(effect_file.read_text(), "first\n")
        checkpoint = await failed.acheckpoint()
        usage = [value for record in checkpoint["records"].values()
                 for value in record.get("agent_usage", {}).values()]
        self.assertTrue(usage)
        self.assertTrue(any(value["calls"] == 1 for value in usage))
        changed = {}
        if damage is not None:
            candidates = {}
            for path in (failed.data.paths.state / "checkpoints/graph/records").glob("*.json"):
                record = json.loads(path.read_text())
                if "agent_usage" in record["value"]:
                    candidates[path] = record
            if damage == "latest":
                latest = max(candidates, key=lambda path: max(v["revision"]
                    for v in candidates[path]["value"]["agent_usage"].values()))
                candidates = {latest: candidates[latest]}
            for path, record in candidates.items():
                value = record["value"]
                if damage in ("envelope", "latest"):
                    del value["agent_usage"]
                else:
                    for saved in value["agent_usage"].values():
                        del saved["calls"]
                # Keep storage revisions valid to reach the Agent's semantic
                # budget validation independently of the file integrity guard.
                from llm.engines.base import EngineEvent, EngineEventType
                with self.app.project_manager.ownership.scope():
                    self.app.run_repository.record_checkpoint(failed.data,
                        EngineEvent(EngineEventType.CHECKPOINT, metadata={
                            "name": "graph", "operation": "record", **record}))
                changed[path] = path.read_bytes()
            self.assertTrue(changed, "Must mutate real persisted Agent usage")
        await self.app.shutdown()
        reopened = LargeLanguageModel(self.root, engines={"graph": self.engine}, components=[
            WorkflowComponent(), RuntimeTools(self.tools), SkillComponent()])
        self.addAsyncCleanup(reopened.shutdown)
        session = (await reopened.projects.aload(self.project.id)).sessions.load(self.session.id)
        if damage is not None:
            rejected = False
            try:
                await (await session.run.resume(failed.id, agent_id="main-agent")).wait()
            except ValueError as error:
                self.assertRegex(str(error), "Tool usage")
                rejected = True
            self.assertEqual(effect_file.read_text(), "first\n", "Agent usage loss admitted a second effect")
            self.assertTrue(rejected, "Corrupt Agent usage must be rejected before creating a Run")
            self.assertEqual(len(model.requests), 2)
            self.assertEqual([run.id for run in await session.run.alist()], [failed.id])
            plan = await session.run.resume_plan(failed.id, agent_id="main-agent")
            self.assertFalse(plan.can_resume)
            self.assertEqual(plan.state, "corrupt")
            self.assertEqual([block.code for block in plan.blockers], ["tool_usage_corrupt"])
            for path, before in changed.items():
                self.assertEqual(path.read_bytes(), before)
            return
        resumed = await (await session.run.resume(failed.id, agent_id="main-agent")).wait()
        self.assertNotEqual(resumed.id, failed.id)
        # Assert the real external effect before status so dropped-limit mutation
        # fails for the safety consequence, not only for an error classification.
        self.assertEqual(effect_file.read_text(), "first\n", "new Tool effect escaped the resumed Agent budget")
        self.assertEqual(resumed.data.status, RunStatus.FAILED, resumed.data.error)
        self.assertEqual(resumed.result.error_code, "tool_budget_exceeded")
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(await (await session.run.aload(failed.id)).acheckpoint(), checkpoint)

    async def test_loop_inside_subworkflow_retries_only_explicit_uncertain_tool(self):
        from tests.llm.test_loop import call
        async def effect(arguments):
            self.effects.append(arguments)
            if len(self.effects) == 1:
                raise RuntimeError('effect receipt lost')
            return None
        self.tools = ToolRegistry((Tool('effect', 'record', {'type':'object'}, effect, contract=TEST_CONTRACT),))
        model = ScriptedCompletion([chunk(calls=[call('{}', name='effect')], finish='tool_calls')],
                                   [chunk('done', finish='stop')])
        handler = AgentNode(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        await self.setup({'main': action_graph('workflow', workflow='child'),
                          'child': action_graph('agent', agent='worker')}, handlers={'agent': handler})
        await self.agents.acreate({'purpose':'work', 'engine':'loop', 'completion':{'model':'test'},
            'tools':['effect'], 'policy':{'max_tool_calls':2}}, identifier='worker')
        failed = await self.request()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        records = (await failed.acheckpoint())['records']
        keys = [k for k,v in records.items() if v.get('node_type') == 'engine_record' and v['status'] == 'started']
        self.assertEqual(len(keys), 1)
        with self.assertRaises(RunRequestError):
            await self.session.run.resume(failed.id, agent_id='main-agent')
        resumed = await (await self.session.run.resume(failed.id, agent_id='main-agent', retry_nodes=keys)).wait()
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(self.effects, [{}, {}])
        self.assertEqual(len(model.requests), 2)

    async def test_nested_expired_approval_renewal_preserves_original_checkpoint(self):
        from dataclasses import replace
        from llm.core.interactions import approval_request
        from llm.services.runtime.tools import ToolApprovalRequired
        from tests.llm.test_loop import call
        async def authorize(call):
            raise ToolApprovalRequired(request=replace(approval_request('Review'),
                expires_at='2000-01-01T00:00:00+00:00'))
        model = ScriptedCompletion([chunk(calls=[call('{}', name='effect')], finish='tool_calls')],
                                   [chunk('done', finish='stop')])
        await self.setup({'main': action_graph('agent', agent='worker')},
            handlers={'agent': AgentNode(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})},
            services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize)))
        await self.agents.acreate({'purpose': 'work', 'engine': 'loop', 'completion': {'model': 'test'},
                                  'tools': ['effect']}, identifier='worker')
        paused = await self.request()
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        self.assertEqual(self.effects, [])
        before = await paused.acheckpoint()
        old, = await paused.ainteractions()
        self.assertTrue(old.expired)
        with self.assertRaises(RunRequestError):
            await self.session.run.resume(paused.id, agent_id='main-agent',
                decisions={old.binding['key']: {'approved': True}})
        renewed = await paused.arenew_interaction(old)
        await paused.arespond(renewed.respond('approve'))
        resumed = await (await self.session.run.resume(paused.id, agent_id='main-agent')).wait()
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(self.effects, [{}])
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(await paused.acheckpoint(), before)
        self.assertEqual([r.request_id for r in await paused.ainteraction_responses()], [renewed.id])

    async def test_loop_agent_denied_approval_never_executes_tool(self):
        from llm.services.runtime.tools import ToolApprovalRequired
        from tests.llm.test_loop import call
        async def authorize(call):
            raise ToolApprovalRequired('review')
        model = ScriptedCompletion([chunk(calls=[call('{}', name='effect')], finish='tool_calls')])
        handler = AgentNode(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        await self.setup({'main': action_graph('agent', agent='worker')}, handlers={'agent': handler},
                         services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize)))
        await self.agents.acreate({'purpose':'work', 'engine':'loop', 'completion':{'model':'test'},
                                  'tools':['effect']}, identifier='worker')
        paused = await self.request()
        keys = [k for k,v in (await paused.acheckpoint())['records'].items() if v.get('approval_required')]
        with self.assertRaisesRegex(RunRequestError, 'selection requires'):
            await self.session.run.resume(paused.id, agent_id='main-agent', decisions={keys[0]:{'approved':True, 'state':{}}})
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond('deny'))
        denied = await (await self.session.run.resume(paused.id, agent_id='main-agent')).wait()
        self.assertEqual(denied.data.status, RunStatus.FAILED)
        self.assertEqual(self.effects, [])
        self.assertEqual(len(model.requests), 1)

    async def test_three_levels_inputs_outputs_and_parent_step_links(self):
        main = action_graph("workflow", workflow="middle", inputs={"request": "/request"}, outputs={"answer": "/result"})
        main.update(initial_state={"request": "nested input", "private": "root"})
        middle = action_graph("workflow", workflow="leaf", inputs={"request": "/request"}, outputs={"result": "/answer"})
        middle["outputs"] = {"result": "/result"}
        leaf = action_graph()
        leaf["outputs"] = {"answer": "/answer"}
        await self.setup({"main": main, "middle": middle, "leaf": leaf}, max_parallelism=1)
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await self.output(run))["answer"], "nested input")
        self.assertEqual(self.effects, [{"request": "nested input"}])
        steps = await run.steps.alist()
        self.assertEqual(len([s for s in steps if s.kind == "subgraph"]), 2)
        self.assertTrue(all(s.run_id == run.id and s.status == StepStatus.COMPLETED for s in steps))
        ids = {s.id for s in steps}
        self.assertTrue(all(s.metadata["parent_step_id"] in ids for s in steps if "parent_step_id" in s.metadata))
        self.assertEqual(len(await self.session.run.alist()), 1)

    async def test_pause_reopen_resume_reuses_completed_child_effects(self):
        child = (WorkflowGraph(entry="first").node("first", "work")
                 .node("second", "work", pause_before=True).node("end", "end")
                 .connect("first", "second").connect("second", "end").to_dict())
        await self.setup({"main": action_graph("workflow", workflow="child"), "child": child}, max_parallelism=1)
        paused = await self.request()
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        checkpoint = await paused.acheckpoint()
        self.assertEqual(checkpoint["records"]['["work","workflow","child","first"]']["status"], "completed")
        await self.app.shutdown()
        reopened = LargeLanguageModel(self.root, engines={"graph": self.engine})
        self.addAsyncCleanup(reopened.shutdown)
        session = await (await reopened.projects.aload(self.project.id)).sessions.aload(self.session.id)
        run = await (await session.run.resume(paused.id, agent_id="main-agent")).wait()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(len(self.effects), 2)
        self.assertEqual((await session.run.aload(paused.id)).data.status, RunStatus.PAUSED)

    async def test_uncertain_leaf_requires_approval_but_container_does_not(self):
        async def work(node):
            self.effects.append(node.node_id)
            if len(self.effects) == 1:
                raise ValueError("effect happened")
            return {"done": True}
        await self.setup({"main": action_graph("workflow", workflow="child"), "child": action_graph()},
                         handlers={"work": work})
        failed = await self.request()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        with self.assertRaises(RunRequestError):
            await self.session.run.resume(failed.id, agent_id="main-agent")
        key = '["work","workflow","child","work"]'
        run = await (await self.session.run.resume(failed.id, agent_id="main-agent", retry_nodes=[key])).wait()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(self.effects, ["work", "work"])

    async def test_changed_child_definition_rejects_resume(self):
        child = action_graph(pause_before=True)
        await self.setup({"main": action_graph("workflow", workflow="child"), "child": child})
        paused = await self.request()
        child["initial_state"] = {"changed": True}
        await self.workflows.asave("child", child)
        with self.assertRaisesRegex(RunRequestError, "changed"):
            await self.session.run.resume(paused.id, agent_id="main-agent")
        self.assertEqual(self.effects, [])

    async def test_custom_graph_wrapper_is_uncertain_unless_explicitly_pure(self):
        async def work(node):
            raise ValueError("child failed")
        child_engine = GraphEngine(handlers={"work": work}).for_agent({"engine": "graph", "engine_options": {"workflow": "child"}})
        class Wrapper:
            def graph_engine(self, definition, capabilities):
                return child_engine
            async def __call__(self, node):
                return await node.invoke_graph(child_engine, node.context, node.inputs, node.emit)
        await self.setup({"main": action_graph("wrapper"), "child": action_graph()}, handlers={"wrapper": Wrapper()})
        failed = await self.request()
        with self.assertRaises(RunRequestError):
            await self.session.run.resume(failed.id, agent_id="main-agent", retry_nodes=['["work","workflow","child","work"]'])
        records = (await failed.acheckpoint())["records"]
        self.assertFalse(records['["work"]']["container"])

    async def test_reference_cycles_and_depth_fail_before_side_effects(self):
        await self.setup({"main": action_graph("workflow", workflow="child"),
                          "child": action_graph("workflow", workflow="main")})
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(await run.steps.alist(), [])
        await self.workflows.asave("child", action_graph())
        await self.project.asave(config={"parameters": {"engines": {"graph": {'policy': {'max_nested_depth': 0}}}}})
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.effects, [])

    async def test_missing_child_handler_preflight_before_parent_effect(self):
        parent = (WorkflowGraph(entry="first").node("first", "work")
                  .node("nested", "workflow", workflow="child").node("end", "end")
                  .connect("first", "nested").connect("nested", "end").to_dict())
        await self.setup({"main": parent, "child": action_graph("missing")})
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.effects, [])
        self.assertEqual(await run.steps.alist(), [])

    async def test_shared_node_limit_includes_children(self):
        await self.setup({"main": action_graph("workflow", workflow="child"), "child": action_graph()}, max_steps=2)
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertIn("limit", run.data.error)
        self.assertEqual(len(self.effects), 1)

    async def test_parallel_same_child_is_isolated_and_shares_slots(self):
        active, peak = 0, 0
        async def work(node):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"value": node.inputs["label"]}
        parent = (WorkflowGraph(entry="fork", initial_state={"a": "A", "b": "B"})
            .node("fork", "parallel", join="join")
            .node("a", "workflow", workflow="child", inputs={"label": "/a"})
            .node("b", "workflow", workflow="child", inputs={"label": "/b"})
            .node("join", "join").node("end", "end")
            .connect("fork", "a").connect("fork", "b").connect("a", "join")
            .connect("b", "join").connect("join", "end").to_dict())
        await self.setup({"main": parent, "child": action_graph()}, handlers={"work": work}, max_parallelism=1)
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        result = (await self.output(run))["branches"]
        self.assertEqual((result["a"]["value"], result["b"]["value"]), ("A", "B"))
        self.assertEqual(peak, 1)
        keys = (await run.acheckpoint())["records"]
        self.assertIn('["fork","branch","a","a","workflow","child","work"]', keys)
        self.assertIn('["fork","branch","b","b","workflow","child","work"]', keys)

    async def test_graph_agent_child_loop_and_dynamic_skill_capability(self):
        model = ScriptedCompletion([chunk("answer"), chunk(finish="stop")])
        leaf = AgentNode(engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        child_engine = GraphEngine(handlers={"agent": leaf})
        outer = AgentNode(engines={"graph_worker": child_engine})
        child = action_graph("agent", agent="writer", inputs={"request": "/request"}, outputs={"answer": "/text"})
        child["outputs"] = {"answer": "/answer"}
        parent = action_graph("agent", agent="coordinator", outputs={"answer": "/data/answer"})
        parent["initial_state"] = {"request": "nested"}
        await self.setup({"main": parent, "child": child}, handlers={"agent": outer}, max_parallelism=1)
        await self.agents.acreate({"purpose": "Coordinate", "engine": "graph_worker", "engine_options": {"workflow": "child"}}, identifier="coordinator")
        await self.agents.acreate({"purpose": "Write", "engine": "loop", "completion": {"model": "test/model"},
            "resources": {"skills": ["rules"]}}, identifier="writer")
        await (await self.project.components.aget("skills")).acreate({"instructions": "Follow these rules"}, identifier="rules")
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await self.output(run))["answer"], "answer")
        self.assertIn("Follow these rules", model.requests[0]["messages"][0]["content"])
        self.assertEqual(json.loads(model.requests[0]["messages"][-1]["content"]), {"request": "nested"})

    async def test_graph_agent_pause_reuses_tool_receipt_under_parent_run_policy(self):
        child = (WorkflowGraph(entry="effect").node("effect", "tool", tool="effect")
                 .node("wait", "work", pause_before=True).node("end", "end")
                 .connect("effect", "wait").connect("wait", "end").to_dict())
        async def work(node):
            return {"done": True}
        child_engine = GraphEngine(handlers={"tool": ToolNode(), "work": work})
        agent = AgentNode(engines={"graph": child_engine})
        await self.setup({"main": action_graph("agent", agent="coordinator"), "child": child},
                         handlers={"agent": agent}, max_parallelism=1,
                         policies={"tools": {"max_calls": 1}})
        await self.agents.acreate({"purpose": "Coordinate", "engine": "graph", "engine_options": {"workflow": "child"}}, identifier="coordinator")
        paused = await self.request()
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        run = await (await self.session.run.resume(paused.id, agent_id="main-agent")).wait()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(len(self.effects), 1)
        step = next(s for s in await run.steps.alist() if s.kind == "agent")
        self.assertNotIn("completed_tools", step.metadata)
        self.assertNotIn("resources", step.metadata)

    async def test_graph_agent_allowlist_validates_before_any_effects(self):
        child_engine = GraphEngine(handlers={"tool": ToolNode()})
        agent = AgentNode(engines={"graph": child_engine})
        await self.setup({"main": action_graph("agent", agent="coordinator"),
            "child": action_graph("tool", tool="effect")}, handlers={"agent": agent})
        invalid = {"purpose": "Coordinate", "engine": "graph", "engine_options": {"workflow": "child"}, "tools": []}
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            await self.agents.acreate(invalid, identifier="coordinator")
        # Current-format fixture from a changed Engine environment: the original
        # selection-time no-effects assertions remain required as well.
        self.app.project_manager.repository.agents.create(self.project.data, invalid, identifier="coordinator")
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.effects, [])
        # The selected Agent fails before its own Step, resources or effects;
        # starting orchestration does not instantiate all hypothetical Agents.
        self.assertTrue(all(step.kind == "graph" for step in await run.steps.alist()))

    async def test_nested_interrupt_cleans_up_and_preserves_queued_request(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        count = 0
        async def work(node):
            nonlocal count
            count += 1
            if count == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            return {"done": True}
        await self.setup({"main": action_graph("workflow", workflow="child"), "child": action_graph()},
                         handlers={"work": work}, max_parallelism=1)
        first = await self.session.run.submit("first", agent_id="main-agent")
        await asyncio.wait_for(entered.wait(), 15)
        second = await self.session.run.submit("second", agent_id="main-agent")
        await self.session.run.interrupt()
        self.assertEqual((await first.wait()).data.status, RunStatus.INTERRUPTED)
        self.assertTrue(closed.is_set())
        self.assertEqual((await second.wait()).data.status, RunStatus.COMPLETED)

    async def test_graph_agent_explicit_retry_uses_new_runs_parent_budget(self):
        async def uncertain(arguments):
            self.effects.append("effect")
            raise ValueError("uncertain remote result")
        self.tools = ToolRegistry((Tool("effect", "uncertain", {"type": "object"}, uncertain, contract=TEST_CONTRACT),))
        child_engine = GraphEngine(handlers={"tool": ToolNode()})
        agent = AgentNode(engines={"graph": child_engine})
        await self.setup({"main": action_graph("agent", agent="coordinator"),
                          "child": action_graph("tool", tool="effect")}, handlers={"agent": agent},
                         policies={"tools": {"max_calls": 1}})
        await self.agents.acreate({"purpose": "Coordinate", "engine": "graph", "engine_options": {"workflow": "child"}}, identifier="coordinator")
        failed = await self.request()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        key = '["work","workflow","child","work"]'
        run = await (await self.session.run.resume(failed.id, agent_id="main-agent", retry_nodes=[key])).wait()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.effects, ["effect", "effect"])
        self.assertIn("uncertain remote result", run.data.error)

    async def test_parent_tool_policy_applies_in_child(self):
        await self.setup({"main": action_graph("workflow", workflow="child"),
                          "child": action_graph("tool", tool="effect")}, handlers={"tool": ToolNode()},
                         policies={"tools": {"allowed_tools": []}})
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.effects, [])

    async def test_nested_confirmation_does_not_grant_tool_permission(self):
        from llm.services.runtime.tools import ToolApprovalRequired
        authorizations = []
        async def ask(call):
            authorizations.append(call)
            raise ToolApprovalRequired('Nested Tool approval')
        await self.setup({'main': action_graph('workflow', workflow='child'),
                          'child': action_graph('tool', tool='effect', pause_before=True)},
                         handlers={'tool': ToolNode()}, services=BackendServices(tool_runtime=ToolRuntime(authorize=ask)))
        confirmation = await self.request()
        paused = await (await self.session.run.resume(confirmation.id, agent_id='main-agent')).wait()
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        request, = await paused.ainteractions(pending_only=True)
        self.assertEqual(request.kind, 'approval')
        self.assertEqual(request.binding['key'], '["work","workflow","child","work"]')
        self.assertEqual(self.effects, [])
        await paused.arespond(request.respond('approve'))
        resumed = await (await self.session.run.resume(paused.id, agent_id='main-agent')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(len(authorizations), 2)

    async def test_parent_tool_budget_is_shared_by_repeated_child_calls(self):
        parent = (WorkflowGraph(entry="first").node("first", "workflow", workflow="child")
                  .node("second", "workflow", workflow="child").node("end", "end")
                  .connect("first", "second").connect("second", "end").to_dict())
        await self.setup({"main": parent, "child": action_graph("tool", tool="effect")},
                         handlers={"tool": ToolNode()}, policies={"tools": {"max_calls": 1}})
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(len(self.effects), 1)
        self.assertIn("budget", run.data.error)

    async def test_parent_timeout_cancels_child_and_finalizes_steps(self):
        closed = asyncio.Event()
        async def work(node):
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        await self.setup({"main": action_graph("workflow", workflow="child"), "child": action_graph()},
                         handlers={"work": work}, timeout_seconds=1)
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertTrue(closed.is_set())
        self.assertTrue(all(s.status not in (StepStatus.RUNNING, StepStatus.PENDING) for s in await run.steps.alist()))

    async def test_nested_loop_resume_uses_distinct_iteration_paths(self):
        body = action_graph("workflow", workflow="child")
        parent = (WorkflowGraph(entry="repeat").node("repeat", "loop", body=body,
                  max_iterations=2, on_limit="continue").node("end", "end").connect("repeat", "end").to_dict())
        await self.setup({"main": parent, "child": action_graph(pause_before=True)})
        first = await self.request()
        second = await (await self.session.run.resume(first.id, agent_id="main-agent")).wait()
        self.assertEqual(second.data.status, RunStatus.PAUSED, second.data.error)
        final = await (await self.session.run.resume(second.id, agent_id="main-agent")).wait()
        self.assertEqual(final.data.status, RunStatus.COMPLETED, final.data.error)
        self.assertEqual(len(self.effects), 2)
        keys = (await final.acheckpoint())["records"]
        for index in (1, 2):
            self.assertIn(f'["repeat",{index},"work","workflow","child","work"]', keys)

    async def test_nested_input_output_schema_fail_without_followup_effects(self):
        child = action_graph()
        child["input_schema"] = {"type": "object", "required": ["required"]}
        await self.setup({"main": action_graph("workflow", workflow="child"), "child": child})
        self.assertEqual((await self.request()).data.status, RunStatus.FAILED)
        self.assertEqual(self.effects, [])
        child.pop("input_schema")
        child["output_schema"] = {"type": "object", "required": ["missing"]}
        await self.workflows.asave("child", child)
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(len(self.effects), 1)
        self.assertTrue(any(s.kind == "subgraph" and s.status == StepStatus.FAILED for s in await run.steps.alist()))

    async def test_actual_nested_process_crash_does_not_replay_completed_node(self):
        script = r'''
import asyncio, os, sys
from pathlib import Path
from llm.llm import LargeLanguageModel
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowGraph
from tests.llm.test_nested_graph import action_graph
root = Path(sys.argv[1])
async def work(node):
    with (root / "effects.txt").open("a") as file:
        file.write(node.node_id + "\n")
        file.flush()
        os.fsync(file.fileno())
    if node.node_id == "second": os._exit(31)
    return {}
async def main():
    async with LargeLanguageModel(root, engines={"graph": GraphEngine(handlers={"work": work})}) as app:
        project = await app.projects.acreate("crash", components=["workflows"])
        workflows = await project.components.aget("workflows")
        await workflows.acreate(action_graph("workflow", workflow="child"), identifier="main")
        child = (WorkflowGraph(entry="first").node("first", "work").node("second", "work")
                 .node("end", "end").connect("first", "second").connect("second", "end"))
        await workflows.acreate(child.to_dict(), identifier="child")
        await project.agents.acreate({"engine": "graph", "engine_options": {"workflow": "main"}}, identifier="main-agent")
        session = await project.sessions.acreate()
        await (await session.run.submit("crash", agent_id="main-agent")).wait()
asyncio.run(main())
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, str(self.root)],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
        self.assertEqual(result.returncode, 31, result.stdout.decode())
        async def work(node):
            with (self.root / "effects.txt").open("a") as file:
                file.write(node.node_id + "\n")
            return {}
        app = LargeLanguageModel(self.root, engines={"graph": GraphEngine(handlers={"work": work})})
        self.addAsyncCleanup(app.shutdown)
        project = (await app.projects.alist())[0]
        session = (await project.sessions.alist())[0]
        await session.run.start()
        await session.run.wait_idle()
        old = (await session.run.alist())[0]
        self.assertEqual(old.data.status, RunStatus.INTERRUPTED)
        self.assertEqual((self.root / "effects.txt").read_text().splitlines(), ["first", "second"])
        with self.assertRaises(RunRequestError):
            await session.run.resume(old.id, agent_id="main-agent")
        run = await (await session.run.resume(old.id, agent_id="main-agent",
            retry_nodes=['["work","workflow","child","second"]'])).wait()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((self.root / "effects.txt").read_text().splitlines(), ["first", "second", "second"])
