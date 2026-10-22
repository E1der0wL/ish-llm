"""Owner boundaries remain valid for substituted engines, handlers and providers."""

from llm.core.model_protocol import ModelMessage, ModelRequest

from llm.providers.litellm import LiteLLMCompletionAdapter

import unittest
import json
import tempfile
from dataclasses import replace
from types import SimpleNamespace
from copy import deepcopy

from llm.components.agents import AgentComponent
from llm.components.tools import Tool, ToolRegistry
from llm.components.workflows.bindings import validate_contract
from llm.core.models import ProjectConfig
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType
from llm.core.interactions import InteractionRequest, InteractionOption
from llm.engines.graph.checkpoints import EngineCheckpointScope
from llm.components.tools import ToolEffect, ToolIsolation
from tests.llm.support.runtime_tools import TEST_CONTRACT


class SchemaBoundaryTests(unittest.TestCase):
    def check(self, schema):
        ToolRegistry((Tool("sample", "sample", schema, lambda args: None, contract=TEST_CONTRACT),))
        AgentComponent().validate_record("sample", {
            "purpose": "sample", "engine": "custom", "input_schema": deepcopy(schema)})
        validate_contract({"input_schema": deepcopy(schema)})

    def test_literal_reference_names_are_data(self):
        for schema in (
            {"type": "object", "properties": {"$ref": {"type": "string"}}},
            {"type": "object", "properties": {"payload": {"const": {"$ref": "literal"}}}},
            {"type": "object", "examples": [{"$dynamicRef": "literal"}]},
            {"type": "object", "properties": {"payload": {"default": {"$ref": "literal"}}}},
        ):
            with self.subTest(schema=schema):
                self.check(schema)

    def test_real_remote_refs_are_rejected_and_local_refs_work(self):
        self.check({"type": "object", "$defs": {"text": {"type": "string"}},
                    "properties": {"payload": {"$ref": "#/$defs/text"}}})
        for key in ("$ref", "$dynamicRef"):
            for check in (lambda s: ToolRegistry((Tool("sample", "", s, lambda a: None, contract=TEST_CONTRACT),)),
                          lambda s: AgentComponent().validate_record("sample", {
                              "purpose": "sample", "engine": "custom", "input_schema": s}),
                          lambda s: validate_contract({"input_schema": s})):
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "local"):
                    check({"type": "object", "properties": {"payload": {key: "https://invalid/schema"}}})

    def test_selected_binding_does_not_silently_allow_identity_repair(self):
        from llm.components.tools import ToolContract
        class Selected:
            def bind_arguments(self, arguments, context):
                return arguments
            async def __call__(self, arguments):
                return None
        with self.assertRaisesRegex(ValueError, "selected Tool bindings.*retry_fields"):
            ToolRegistry((Tool("select", "", {"type": "object", "properties": {"target": {"type": "string"}}},
                Selected(), contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=False, retry_fields=('target',))),))




class CheckpointBoundaryTests(unittest.TestCase):
    def test_private_state_requires_explicit_lifecycle(self):
        from llm.core.checkpoints import checkpoint_lifecycle
        with self.assertRaisesRegex(ValueError, "explicit lifecycle"):
            checkpoint_lifecycle({"status": "private-done"})
        self.assertEqual(checkpoint_lifecycle({"status": "private-done"}, {"status": "completed", "container": True}),
                         ("completed", True))

    def test_arbitrary_parent_key_and_input_roundtrip_preserve_choice(self):
        scope = EngineCheckpointScope("opaque:not-json", "child", {}, parent_name="custom-parent")
        scope.accepted(scope.wrap(EngineEvent(EngineEventType.CHECKPOINT, metadata={
            "name": "child", "operation": "initialize", "header": {}})))
        request = InteractionRequest("Where?", (
            InteractionOption("city", "City", value="input"),
            InteractionOption("address", "Address", value="input")), kind="input",
            input_schema={"type": "string"}).bind("child", "question")
        scope.accepted(scope.wrap(EngineEvent(EngineEventType.CHECKPOINT, metadata={
            "name": "child", "operation": "record", "key": "question",
            "value": {"status": "private-await", "interaction": request.to_dict()},
            "lifecycle": {"status": "waiting", "container": True}})))
        parent = InteractionRequest.from_dict(scope.records[scope.key("question")]["interaction"])
        decision = parent.respond("address", value="Seoul").decision(parent)
        self.assertEqual(parent.select_decision(decision)[0].id, "address")
        context = EngineContext(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(metadata={
            "resume": {"decisions": {scope.key("question"): decision}, "retry_nodes": []}}), ())
        child = scope.context(context)
        self.assertEqual(child.run.metadata["resume"]["decisions"], {"question": "Seoul"})
        self.assertEqual(child.checkpoint["records"]["question"]["status"], "private-await")
        for bad in ("Seoul", {"option_id": "address"}, {**decision, "approved": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parent.select_decision(bad)


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_alternative_adapter_owns_request_and_retry_fields(self):
        from llm.providers.observations import ModelCall
        from llm.core.model_protocol import CompletionUpdate, ModelResponse, StopReason
        from llm.policies.retry import RetryDecision
        requests = []

        class Decoder:
            def decode(self, chunk):
                yield CompletionUpdate(text=chunk)
            def finish(self):
                return ModelResponse(ModelMessage("assistant", "ok"), StopReason.COMPLETE)

        class Adapter:
            def prepare(self, request):
                self.assert_keys = set(request.parameters)
                return request
            def describe_call(self, request):
                return ModelCall("custom", "custom-model", lambda c: 1, lambda r: {}, lambda e: "custom_transient")
            def describe_request(self, request):
                return {"model": "custom-model"}
            def effective_attempts(self, request, maximum):
                return maximum
            def classify_error(self, error):
                return "custom_transient"
            def retryable(self, error):
                return isinstance(error, ConnectionError)
            def retry_fields(self, request):
                return ("deadline",)
            def validate_retry(self, request, *, original):
                if request.parameters.keys() != original.parameters.keys() or request.messages != original.messages:
                    raise ValueError("Changed input")
            def decoder(self, **limits):
                return Decoder()
            async def stream(self, request, *, buffer_size):
                requests.append(deepcopy(request))
                if len(requests) == 1:
                    raise ConnectionError("retry")
                yield "ok"

        adapter = Adapter()
        engine = BaseEngine(completion_adapter=adapter)
        response = []
        async def decide(failure):
            return RetryDecision(changes={"deadline": 2})
        events = [e async for e in engine.stream_completion(ModelRequest([ModelMessage("user", "question")], {"deadline": 1}),
            response=response, retry_decider=decide, provider={"max_attempts": 2})]
        self.assertEqual(response, [ModelResponse(ModelMessage("assistant", "ok"), StopReason.COMPLETE)])
        self.assertIn("ok", events)
        self.assertEqual(requests, [ModelRequest([ModelMessage("user", "question")], {"deadline": 1}),
                                    ModelRequest([ModelMessage("user", "question")], {"deadline": 2})])


class SelectedAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_agent_inside_delegated_graph_is_revalidated(self):
        from llm.llm import LargeLanguageModel
        from llm.components.workflows import WorkflowGraph, WorkflowComponent
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.agent import AgentNode
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion, chunk, call
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        parent = ScriptedCompletion([chunk(calls=[call('{"agent_id":"graph-worker","input":{}}', name="agent_run")], finish="tool_calls")],
                                    [ConnectionError("after Graph completion")])
        leaf = ScriptedCompletion([chunk("leaf done", finish="stop")])
        engines = {"parent": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=parent)), "leaf": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=leaf))}
        engines["graph"] = GraphEngine(handlers={"agent": AgentNode(engines=engines)})
        async with LargeLanguageModel(directory.name, engines=engines, components=[AgentComponent(engines=engines), WorkflowComponent()]) as app:
            project = await app.projects.acreate(components=["agents", "workflows"], config={"parameters": {"engines": {
                name: {"config": {"completion": {"model": "test"}}} for name in ("parent", "leaf")}}})
            await project.components.workflows.acreate(WorkflowGraph(entry="leaf").node("leaf", "agent", agent="leaf")
                .node("end", "end").connect("leaf", "end").to_dict(), identifier="flow")
            agents = project.components.agents
            await agents.acreate({"purpose": "graph", "engine": "graph", "engine_options": {"workflow": "flow"}}, identifier="graph-worker")
            await agents.acreate({"purpose": "leaf", "engine": "leaf"}, identifier="leaf")
            session = await project.sessions.acreate()
            run = await (await session.run.submit("go", engine="parent")).wait()
            self.assertEqual(run.data.status, "failed", run.data.error)
            self.assertEqual(len(leaf.requests), 1)
            snapshot = await agents.asnapshot("leaf")
            await agents.arevise("leaf", {**snapshot["definition"], "purpose": "changed"}, expected_revision=snapshot["revision"])
            with self.assertRaisesRegex(Exception, "changed"):
                await session.run.resume(run.id, engine="parent")
            self.assertEqual(len(leaf.requests), 1)

    async def test_delegation_resume_checks_selected_agent_not_catalog(self):
        from tests.llm.test_agent_delegation import AgentDelegationTests
        from tests.llm.test_loop import chunk, call
        from llm.components.tools import ToolContract
        effects = []
        async def write(arguments):
            effects.append(arguments)
            return "done"
        registry = ToolRegistry((Tool("write", "write", {"type": "object"}, write,
                                      contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=())),))
        _, session, parent, child = await AgentDelegationTests.setup(self,
            registry=registry, child_tools=("write",), child_responses=[
                [chunk(calls=[call("{}", name="write")], finish="tool_calls")],
                [chunk("done", finish="stop")]])
        agents = session.project.components.agents
        await agents.acreate({"purpose": "unused", "engine": "child",
                             "engine_options": {"invalid_for_child": True},
                             "resources": {"rag": True}}, identifier="unused")
        run = await (await session.run.submit("go", engine="parent")).wait()
        self.assertEqual(run.data.status, "paused", run.data.error)
        snapshot = await agents.asnapshot("unused")
        await agents.arevise("unused", {**snapshot["definition"], "purpose": "changed"}, expected_revision=snapshot["revision"])
        request, = await run.ainteractions(pending_only=True)
        await run.arespond(request.respond("approve"))
        resumed = await (await session.run.resume(run.id, engine="parent")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(effects, [{}])
        self.assertEqual((len(parent.requests), len(child.requests)), (2, 2))

    async def test_selected_agent_change_rejects_waiting_and_completed_delegation(self):
        from tests.llm.test_agent_delegation import AgentDelegationTests
        from tests.llm.test_loop import chunk, call
        from llm.components.tools import ToolContract
        for waiting in (True, False):
            with self.subTest(waiting=waiting):
                async def write(arguments):
                    return None
                registry = ToolRegistry((Tool("write", "write", {"type": "object"}, write,
                    contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=waiting, operation_key_required=False, retry_fields=())),))
                _, session, parent, _ = await AgentDelegationTests.setup(self,
                    registry=registry, child_tools=("write",), child_responses=[
                        [chunk(calls=[call("{}", name="write")], finish="tool_calls")],
                        [chunk("done", finish="stop")]])
                if not waiting:
                    parent.responses[1] = [ConnectionError("stop after child")]
                run = await (await session.run.submit("go", engine="parent")).wait()
                self.assertIn(run.data.status, ("paused", "failed"), run.data.error)
                if waiting:
                    request, = await run.ainteractions(pending_only=True)
                    await run.arespond(request.respond("approve"))
                agents = session.project.components.agents
                snapshot = await agents.asnapshot("worker")
                await agents.arevise("worker", {**snapshot["definition"], "purpose": "changed"}, expected_revision=snapshot["revision"])
                with self.assertRaisesRegex(Exception, "changed"):
                    await session.run.resume(run.id, engine="parent")


    async def test_completed_nested_selection_is_checked_before_resume(self):
        from tests.llm.test_agent_delegation import AgentDelegationTests
        from tests.llm.test_loop import chunk, call
        _, session, parent, child = await AgentDelegationTests.setup(self, child_tools=("agent_run",), child_responses=[
            [chunk(calls=[call('{"agent_id":"leaf","input":{}}', name="agent_run")], finish="tool_calls")],
            [chunk("leaf done", finish="stop")], [chunk("worker done", finish="stop")]])
        agents = session.project.components.agents
        await agents.acreate({"purpose": "leaf", "engine": "child"}, identifier="leaf")
        parent.responses[1] = [ConnectionError("after completed children")]
        run = await (await session.run.submit("go", engine="parent")).wait()
        self.assertEqual(run.data.status, "failed", run.data.error)
        self.assertEqual(len(child.requests), 3)
        before = await agents.asnapshot("leaf")
        await agents.arevise("leaf", {**before["definition"], "purpose": "changed leaf"}, expected_revision=before["revision"])
        with self.assertRaisesRegex(Exception, "changed"):
            await session.run.resume(run.id, engine="parent")
        self.assertEqual(len(child.requests), 3)

    async def test_delegation_waiting_before_child_execution_records_selection(self):
        from tests.llm.test_agent_delegation import AgentDelegationTests
        from llm.services.runtime.tools import ToolRuntime, ToolApprovalRequired
        app, session, _, child = await AgentDelegationTests.setup(self)
        def authorize(call):
            raise ToolApprovalRequired()
        # Host mechanism requests approval before the selected child starts.
        app.services.tool_runtime = ToolRuntime(authorize=authorize)
        run = await (await session.run.submit("go", engine="parent")).wait()
        self.assertEqual(run.data.status, "paused", run.data.error)
        self.assertEqual(child.requests, [])
        request, = await run.ainteractions(pending_only=True)
        await run.arespond(request.respond("approve"))
        agents = session.project.components.agents
        before = await agents.asnapshot("worker")
        await agents.arevise("worker", {**before["definition"], "purpose": "changed"}, expected_revision=before["revision"])
        with self.assertRaisesRegex(Exception, "changed"):
            await session.run.resume(run.id, engine="parent")
        self.assertEqual(child.requests, [])


class GraphBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def setup_graph(self, definition, handlers, tools=(), *, agents=(), components=()):
        from llm.llm import LargeLanguageModel
        from llm.engines.graph import GraphEngine
        from llm.components.workflows import WorkflowComponent
        from tests.llm.support.runtime_tools import RuntimeTools
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        registry = ToolRegistry(tuple(tools))
        app = LargeLanguageModel(directory.name, engines={"graph": GraphEngine(handlers=handlers)}, components=[
            WorkflowComponent(), AgentComponent(), RuntimeTools(registry), *components])
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate("test", components=["workflows", "agents", "tools", *[c.name for c in components]])
        await project.components.workflows.acreate(definition, identifier="flow")
        for identifier, profile in agents:
            await project.components.agents.acreate(profile, identifier=identifier)
        await project.components.tools.aenable(*registry.names())
        session = await project.sessions.acreate("test")
        return app, project, session

    async def test_engine_owns_instruction_composition_without_loop_config_layout(self):
        from copy import copy
        from llm.components.workflows import WorkflowGraph
        from llm.components.prompts import PromptComponent
        from llm.engines.graph.agent import AgentNode
        seen = []
        class Custom(BaseEngine):
            def for_agent(self, definition):
                return copy(self)
            def resolve_config(self, config, name, *, session_config=None):
                return {"values": {"config": {"private_instructions": "native guide"}}}
            def with_instructions(self, context, *, before=(), after=()):
                worker = copy(self)
                worker.instructions = (*before, "native guide", *after)
                return worker
            async def run(self, context):
                seen.append(self.instructions)
                yield "done"
        graph = WorkflowGraph(entry="a").node("a", "agent", agent="worker").node("end", "end").connect("a", "end")
        _, project, session = await self.setup_graph(graph.to_dict(), {"agent": AgentNode(engines={"custom": Custom()})},
            components=(PromptComponent(),), agents=(("worker", {"purpose": "work", "engine": "custom", "resources": {"prompt": "guide"}}),))
        await project.components.prompts.acreate({"messages": [{"role": "system", "content": "resource guide"}]}, identifier="guide")
        run = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(seen, [("system: resource guide", "native guide")])

    async def test_tool_handler_registration_name_does_not_change_approval(self):
        from llm.components.workflows import WorkflowGraph
        from llm.engines.graph.tool import ToolNode
        from llm.components.tools import ToolContract
        effects = []
        async def write(arguments):
            effects.append(arguments)
            return "done"
        graph = WorkflowGraph(entry="write").node("write", "execute", tool="write").node("end", "end").connect("write", "end")
        _, _, session = await self.setup_graph(graph.to_dict(), {"execute": ToolNode()}, (
            Tool("write", "write", {"type": "object"}, write, contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=())),))
        run = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(run.data.status, "paused", run.data.error)
        self.assertEqual(effects, [])
        request, = await run.ainteractions(pending_only=True)
        await run.arespond(request.respond("approve"))
        resumed = await (await session.run.resume(run.id, engine="graph")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(effects, [{}])

    async def test_graph_agent_future_definition_can_change_but_selected_cannot(self):
        from llm.components.workflows import WorkflowGraph
        from llm.engines.graph.agent import AgentNode
        from llm.engines.loop import LoopEngine
        from llm.components.tools import ToolContract
        from tests.llm.test_loop import ScriptedCompletion, chunk, call
        effects = []
        async def write(arguments):
            effects.append(1)
            return "done"
        model = ScriptedCompletion([chunk(calls=[call("{}", name="write")], finish="tool_calls")],
                                   [chunk("A done", finish="stop")], [chunk("B done", finish="stop")])
        graph = (WorkflowGraph(entry="a").node("a", "agent", agent="a")
                 .node("b", "agent", agent="b").node("end", "end").connect("a", "b").connect("b", "end"))
        profile = {"purpose": "test", "engine": "loop", "completion": {"model": "test"}}
        _, project, session = await self.setup_graph(graph.to_dict(), {"agent": AgentNode(engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})},
            (Tool("write", "write", {"type": "object"}, write, contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=())),),
            agents=(("a", {**profile, "tools": ["write"]}), ("b", profile)))
        run = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(run.data.status, "paused", run.data.error)
        agents = project.components.agents
        before = await agents.asnapshot("a")
        await agents.arevise("a", {**before["definition"], "purpose": "changed"}, expected_revision=before["revision"])
        request, = await run.ainteractions(pending_only=True)
        await run.arespond(request.respond("approve"))
        with self.assertRaisesRegex(Exception, "changed"):
            await session.run.resume(run.id, engine="graph")
        changed = await agents.asnapshot("a")
        await agents.arevise("a", before["definition"], expected_revision=changed["revision"])
        future = await agents.asnapshot("b")
        await agents.arevise("b", {**future["definition"], "system_prompt": "new B instructions"}, expected_revision=future["revision"])
        resumed = await (await session.run.resume(run.id, engine="graph")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(effects, [1])
        self.assertEqual(model.requests[-1]["messages"][0]["content"], "new B instructions")

    async def test_sibling_tool_call_ids_resolve_by_completed_step(self):
        from llm.components.workflows import WorkflowGraph
        from llm.engines.graph.agent import AgentNode
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion, chunk, call
        async def echo(arguments):
            return arguments["value"]
        model = ScriptedCompletion(
            [chunk(calls=[call('{"value":"first"}', name="echo")], finish="tool_calls")], [chunk("one", finish="stop")],
            [chunk(calls=[call('{"value":"second"}', name="echo")], finish="tool_calls")], [chunk("two", finish="stop")])
        graph = (WorkflowGraph(entry="a").node("a", "agent", agent="worker").node("b", "agent", agent="worker")
                 .node("end", "end").connect("a", "b").connect("b", "end"))
        app, project, session = await self.setup_graph(graph.to_dict(), {"agent": AgentNode(engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})},
            (Tool("echo", "echo", {"type": "object", "properties": {"value": {"type": "string"}}}, echo, contract=TEST_CONTRACT),),
            agents=(("worker", {"purpose": "echo", "engine": "loop", "completion": {"model": "test"}, "tools": ["echo"]}),))
        run = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(run.data.status, "completed", run.data.error)
        steps = [s for s in await run.steps.alist() if s.kind == "tool"]
        self.assertEqual([s.metadata["tool_call_id"] for s in steps], ["call_1", "call_1"])
        query = app.project_manager.results
        results = [query.tool_result(project.data, session.id, run.id, s.id, steps=app.step_manager) for s in steps]
        self.assertEqual({r["content"] for r in results}, {"first", "second"})
        non_tool = next(s for s in await run.steps.alist() if s.kind != "tool")
        with self.assertRaises(ValueError):
            query.tool_result(project.data, session.id, run.id, non_tool.id, steps=app.step_manager)

    async def test_completed_graph_agent_is_checked_before_later_node_resume(self):
        from llm.components.workflows import WorkflowGraph
        from llm.engines.graph.agent import AgentNode
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion, chunk
        model = ScriptedCompletion([chunk("A done", finish="stop")], [chunk("B done", finish="stop")])
        graph = (WorkflowGraph(entry="a").node("a", "agent", agent="a")
                 .node("b", "agent", agent="b", pause_before=True).node("end", "end")
                 .connect("a", "b").connect("b", "end"))
        profile = {"purpose": "test", "engine": "loop", "completion": {"model": "test"}}
        _, project, session = await self.setup_graph(graph.to_dict(), {"agent": AgentNode(engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})},
            agents=(("a", profile), ("b", profile)))
        run = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(run.data.status, "paused", run.data.error)
        self.assertEqual(len(model.requests), 1)
        request, = await run.ainteractions(pending_only=True)
        await run.arespond(request.respond("approve"))
        snapshot = await project.components.agents.asnapshot("a")
        await project.components.agents.arevise("a", {**snapshot["definition"], "purpose": "changed completed"},
                                                expected_revision=snapshot["revision"])
        with self.assertRaisesRegex(Exception, "changed"):
            await session.run.resume(run.id, engine="graph")
        self.assertEqual(len(model.requests), 1)
