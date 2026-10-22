"""Selected execution, durable receipts and owner-defined observations."""

from llm.core.model_protocol import ModelMessage

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llm.components.base import Component
from llm.components.registry import ComponentRegistry
from llm.components.processing import CompletionMessage, CompletionRequest, CompletionPipeline, CompletionSession
from llm.core.contracts import Diagnostic
from llm.core.interactions import InteractionRequest, InteractionOption
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.errors import CodedError
from llm.llm import LargeLanguageModel
from llm.services.runtime.interactions import InteractionRepository
from tests.llm.test_loop import chunk, call


class ObservationTests(unittest.TestCase):
    def test_broken_error_attributes_preserve_original_and_cause(self):
        from llm.errors import stable_error_code
        class Broken(Exception):
            @property
            def code(self):
                raise RuntimeError("code observer failed")
            @property
            def diagnostic(self):
                raise RuntimeError("diagnostic observer failed")
        class TrustedBroken(Broken, CodedError):
            pass
        for kind in (Broken, TrustedBroken):
            with self.subTest(kind=kind):
                error = kind("original failure")
                self.assertIsNone(stable_error_code(error))
                value = Diagnostic.from_exception(error, code="engine_failed")
                self.assertEqual((value.code, value.message), ("engine_failed", "original failure"))
                class Cause(CodedError, Exception):
                    code = "original_cause"
                error.__cause__ = Cause("cause")
                self.assertEqual(stable_error_code(error), "original_cause")
                self.assertEqual(Diagnostic.from_exception(error).code, "original_cause")

    def test_agent_factory_cannot_change_declared_steering_mode(self):
        from llm.core.steering import SteeringMode
        from llm.engines.agents import AgentExecution
        for registered, selected in ((SteeringMode.CONSUME, SteeringMode.UNSUPPORTED),
                                     (SteeringMode.UNSUPPORTED, SteeringMode.CONSUME),
                                     (SteeringMode.FORWARD, SteeringMode.CONSUME)):
            class Bound:
                steering_mode = selected
                checkpoint_name = "custom"
                def validate_resume(self, checkpoint, **kwargs):
                    pass
                async def execute(self, context):
                    if False:
                        yield
            class Factory(Bound):
                steering_mode = registered
                def for_agent(self, definition):
                    return Bound()
            runtime = AgentExecution(engines={"custom": Factory()})
            with self.subTest(registered=registered, selected=selected), self.assertRaisesRegex(ValueError, "preserve Engine steering mode"):
                runtime._engine({"engine": "custom"})

    def test_external_component_hooks_do_not_require_local_storage(self):
        from llm.services.runtime.usage import component_usage
        from llm.services.history.retention import HistoryRetention
        class External(Component):
            name = directory = "remote"
            def model_usage(self, project):
                return [{"model": "remote-model"}]
            def history_references(self, project):
                return {"run_ids": ["remote-run"]}
            def references(self, project):
                return [{"component": "data", "identifier": "source", "source": "remote-record"}]
        with tempfile.TemporaryDirectory() as root:
            registry = ComponentRegistry((External(),))
            project = SimpleNamespace(paths=SimpleNamespace(root=Path(root)),
                                      initialized_components=("remote",), components=())
            self.assertFalse((Path(root) / "remote").exists())
            self.assertEqual(component_usage(registry, project), [{"model": "remote-model"}])
            retention = HistoryRetention(SimpleNamespace(components=registry))
            self.assertEqual(retention._references(project)["run_ids"], {"remote-run"})
            self.assertEqual(registry.dependents(project, "data", "source"),
                             [{"component": "remote", "identifier": "remote-record"}])

    def test_confirmation_uses_semantic_approval_including_nested_selection(self):
        request = InteractionRequest("Continue", (
            InteractionOption("continue", "Continue", effect="approve", value=True),
            InteractionOption("stop", "Stop", effect="deny", value=False)), kind="confirmation")
        for selection in (False, True):
            bound = request.bind("custom", "wait", selection=selection)
            decisions, receipts, _ = InteractionRepository().decisions([bound], [], {}, confirm=True)
            self.assertEqual(receipts[0].option_id, "continue")
            self.assertEqual(decisions["wait"], bound.decision_for("continue"))
        ambiguous = replace(request, options=(*request.options,
            InteractionOption("elsewhere", "Elsewhere", effect="approve", value="elsewhere"))).bind("custom", "wait")
        self.assertIsNone(ambiguous.find_approval())
        with self.assertRaisesRegex(ValueError, "explicit option"):
            InteractionRepository().decisions([ambiguous], [], {}, confirm=True)
        needs_input = replace(request, input_schema={"type": "object", "required": ["destination"],
            "properties": {"destination": {"type": "string"}}, "additionalProperties": False}).bind("custom", "wait", selection=True)
        self.assertIsNone(needs_input.find_approval())
        with self.assertRaisesRegex(ValueError, "required input"):
            InteractionRepository().decisions([needs_input], [], {}, confirm=True)

    def test_custom_engine_execution_binding_is_owned_by_selected_engine(self):
        from llm.engines.agents import AgentExecution
        class Custom:
            required_capabilities = ()
            def for_agent(self, definition):
                return self
            def bind_execution(self, context):
                return {"private_revision": "custom"}
            async def execute(self, context):
                if False:
                    yield
        runtime = AgentExecution(engines={"custom": Custom()})
        context = SimpleNamespace(capabilities={"agents": [{"records": {
            "worker": {"purpose": "work", "engine": "custom"}}}]},
            tools=SimpleNamespace(select=lambda names: None), tool_scope=None)
        self.assertEqual(runtime.binding({"agent": "worker"}, context)["execution"], {"private_revision": "custom"})


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_broken_code_observer_does_not_stop_queue_or_shutdown(self):
        class Broken(Exception):
            @property
            def code(self):
                raise RuntimeError("code observer failed")
        for with_step in (False, True):
            with self.subTest(with_step=with_step), tempfile.TemporaryDirectory() as root:
                entered, release = asyncio.Event(), asyncio.Event()
                effects = []
                async def work(context):
                    message = next(m for m in context.messages if m.id == context.run.input_message_id)
                    if message.content == "first":
                        entered.set()
                        await release.wait()
                        raise Broken("original failure")
                    effects.append(message.content)
                class Raw:
                    async def execute(self, context):
                        await work(context)
                        if False:
                            yield
                engine = BaseEngine(action=work) if with_step else Raw()
                async with LargeLanguageModel(root, engines={"custom": engine}) as app:
                    project = await app.projects.acreate("test", components=[])
                    session = await project.sessions.acreate()
                    first = await session.run.submit("first", engine="custom")
                    await asyncio.wait_for(entered.wait(), 5)
                    second = await session.run.submit("second", engine="custom")
                    release.set()
                    failed = await first.wait(timeout=5)
                    completed = await second.wait(timeout=5)
                    result = await failed.aresult()
                    self.assertEqual(result.error_code, "engine_failed")
                    self.assertEqual(result.diagnostic.message, "original failure")
                    self.assertEqual(completed.data.status, "completed")
                    self.assertEqual(effects, ["second"])
                    if with_step:
                        step, = await failed.steps.alist()
                        self.assertEqual(step.status, "failed")
                        self.assertEqual(step.diagnostic.message, "original failure")

    async def test_custom_event_cannot_forge_predispatch_restart_proof(self):
        from llm.services.composition import BackendServices
        from llm.services.runtime.events import EventHandlers
        effects = []
        class Custom:
            async def execute(self, context):
                effects.append("already executed")
                yield EngineEvent("custom.proof")
        handlers = EventHandlers()
        async def forge(context, event):
            await context.update_metadata({"execution_started": False})
        handlers.register("custom.proof", forge)
        with tempfile.TemporaryDirectory() as root:
            app = LargeLanguageModel(root, engines={"custom": Custom()},
                                     services=BackendServices(event_handlers=handlers))
            try:
                project = await app.projects.acreate("test", components=[])
                session = await project.sessions.acreate()
                run = await (await session.run.submit("go", engine="custom")).wait()
                self.assertEqual(run.data.status, "failed")
                self.assertIn("execution_started", run.data.error)
                self.assertTrue(run.data.metadata["execution_started"])
                self.assertEqual(await run.steps.alist(), [])
                self.assertFalse((await session.run.resume_plan(run.id, engine="custom")).can_restart)
                self.assertEqual(effects, ["already executed"])
            finally:
                await app.shutdown()

    async def test_ambiguous_project_auto_approval_remains_pending(self):
        class Choice(BaseEngine):
            checkpoint_name = "choice"
            async def execute(self, context):
                request = InteractionRequest("Destination", tuple(
                    InteractionOption(name, name, effect="approve", value={"destination": name})
                    for name in ("east", "west")), kind="choice", category="routing",
                    risk_scheme="app", risk=1).bind("choice", "wait", selection=True)
                yield EngineEvent(EngineEventType.CHECKPOINT, metadata={"name": "choice", "operation": "initialize",
                    "header": {"input_message_id": context.run.input_message_id,
                               "message_ids": [m.id for m in context.messages]}})
                yield EngineEvent(EngineEventType.CHECKPOINT, metadata={"name": "choice", "operation": "record",
                    "key": "wait", "value": {"status": "waiting", "interaction": request.to_dict()}})
                yield EngineEvent(EngineEventType.PAUSED, metadata={"checkpoint": "choice"})
        with tempfile.TemporaryDirectory() as root:
            app = LargeLanguageModel(root, engines={"choice": Choice()})
            try:
                project = await app.projects.acreate("test", components=[], config={"policies": {"approval": {
                    "enabled": True, "risk_scheme": "app", "rules": [{"id": "route", "category": "routing", "max_risk": 1}]}}})
                session = await project.sessions.acreate()
                run = await (await session.run.submit("go", engine="choice")).wait()
                self.assertEqual(run.data.status, "paused", run.data.error)
                self.assertEqual(await run.ainteraction_responses(), [])
                self.assertEqual(len(await run.ainteractions(pending_only=True)), 1)
            finally:
                await app.shutdown()

    async def test_base_engine_preserves_computed_diagnostic_without_code_promotion(self):
        class Failure(CodedError, RuntimeError):
            code = "custom_failure"
            @property
            def diagnostic(self):
                return Diagnostic(self.code, "failed", details={"owner_fact": "preserved"})
        async def fail(context):
            raise Failure("failed")
        with tempfile.TemporaryDirectory() as root:
            app = LargeLanguageModel(root, engines={"custom": BaseEngine(action=fail)})
            try:
                project = await app.projects.acreate("test", components=[])
                session = await project.sessions.acreate()
                run = await (await session.run.submit("go", engine="custom")).wait()
                result = await run.aresult()
                self.assertEqual(result.error_code, "custom_failure")
                self.assertEqual(result.diagnostic.details, {"owner_fact": "preserved"})
                for step in await run.steps.alist():
                    self.assertEqual(step.metadata["diagnostic"]["details"], {"owner_fact": "preserved"})
                    self.assertEqual(step.metadata["diagnostic"]["source"]["step_id"], step.id)
            finally:
                await app.shutdown()

    async def test_selected_child_adapter_change_rejects_waiting_approval(self):
        from tests.llm.test_agent_delegation import AgentDelegationTests
        from llm.services.runtime.tools import ToolRuntime, ToolApprovalRequired
        app, session, _, model = await AgentDelegationTests.setup(self)
        adapter = app.engines.get("child").completion_adapter
        adapter.execution_binding = {"revision": "one"}
        def authorize(invocation):
            raise ToolApprovalRequired()
        app.services.tool_runtime = ToolRuntime(authorize=authorize)
        run = await (await session.run.submit("go", engine="parent")).wait()
        self.assertEqual(run.data.status, "paused", run.data.error)
        request, = await run.ainteractions(pending_only=True)
        await run.arespond(request.respond("approve"))
        adapter.execution_binding = {"revision": "two"}
        with self.assertRaisesRegex(Exception, "changed"):
            await session.run.resume(run.id, engine="parent")
        self.assertEqual(model.requests, [])

    async def test_unselected_agent_private_configuration_is_not_validated(self):
        from tests.llm.test_extension_contracts import GraphBoundaryTests
        from llm.components.workflows import WorkflowGraph
        from llm.engines.graph.agent import AgentNode
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion
        model = ScriptedCompletion([chunk("selected", finish="stop")])
        graph = (WorkflowGraph(entry="route").node("route", "branch", cases=[
            {"when": {"path": "/which", "op": "eq", "value": "good"}, "port": "good"}], default="bad")
            .node("good", "agent", agent="good").node("bad", "agent", agent="bad").node("end", "end")
            .connect("route", "good", port="good").connect("route", "bad", port="bad")
            .connect("good", "end").connect("bad", "end").to_dict())
        graph["initial_state"] = {"which": "good"}
        _, _, session = await GraphBoundaryTests.setup_graph(self, graph,
            {"agent": AgentNode(engines={"child": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})}, agents=(
                ("good", {"purpose": "good", "engine": "child", "completion": {"model": "test"}}),
                ("bad", {"purpose": "bad", "engine": "child", "engine_options": {"config": {"typo": True}}})))
        run = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(len(model.requests), 1)

    async def test_step_and_tool_receipt_fail_atomically_without_automatic_replay(self):
        from tests.llm.test_long_running import LongRunningTests
        effects = []
        async def act(arguments):
            effects.append(1)
            return "effect"
        app, session, model = await LongRunningTests.setup_app(self, act, [
            [chunk(calls=[call("{}", name="act")], finish="tool_calls")],
            [chunk("done", finish="stop")]])
        original = app.step_manager.complete
        def fail_once(step):
            if step.kind == "tool":
                raise OSError("completion persistence failure")
            return original(step)
        with patch.object(app.step_manager, "complete", side_effect=fail_once):
            run = await (await session.run.submit("go", engine="loop")).wait()
        self.assertEqual(run.data.status, "failed")
        snapshot = app.run_repository.checkpoint(run.data, "loop")
        self.assertEqual(snapshot["records"]["tool:1:call_1"]["status"], "started")
        with self.assertRaises(Exception):
            await session.run.resume(run.id, engine="loop")
        self.assertEqual(effects, [1])
        self.assertEqual(len(model.requests), 1)

    async def test_adapter_owns_processor_retry_contract(self):
        class Session(CompletionSession):
            async def prepare(self, request):
                request.parameters["retry_budget"] = 7
                if False:
                    yield
        processor = SimpleNamespace(name="custom", priority=0, close_timeout=None, session=lambda c: Session())
        original = [CompletionMessage(ModelMessage("user", "go"), "input")]
        request = CompletionRequest({"retry_budget": 0}, deepcopy(original), 1)
        seen = []
        def validate(parameters, *, original):
            seen.append(parameters["retry_budget"])
            if parameters["retry_budget"] != original["retry_budget"]:
                raise ValueError("Adapter retry budget changed")
        context = SimpleNamespace(run=SimpleNamespace(input_message_id="input"))
        async with CompletionPipeline([processor], context, validate_parameters=validate) as pipeline:
            with self.assertRaisesRegex(ValueError, "retry budget"):
                async for _ in pipeline.prepare(request, original):
                    pass
        self.assertEqual(seen, [7])

    async def test_checkpoint_failure_rolls_back_step_completion_and_output(self):
        from tests.llm.test_long_running import LongRunningTests
        effects = []
        async def act(arguments):
            effects.append(1)
            return "effect"
        app, session, _ = await LongRunningTests.setup_app(self, act, [
            [chunk(calls=[call("{}", name="act")], finish="tool_calls")]])
        record = app.run_repository.record_checkpoint
        def fail(run, event):
            if event.metadata.get("value", {}).get("status") == "completed":
                raise OSError("receipt write failed")
            return record(run, event)
        with patch.object(app.run_repository, "record_checkpoint", side_effect=fail):
            run = await (await session.run.submit("go", engine="loop")).wait()
        self.assertEqual(run.data.status, "failed")
        step, = [s for s in await run.steps.alist() if s.kind == "tool"]
        self.assertNotEqual(step.status, "completed")
        self.assertNotIn("output", step.metadata)
        snapshot = app.run_repository.checkpoint(run.data, "loop")
        self.assertEqual(snapshot["records"]["tool:1:call_1"]["status"], "started")
        self.assertEqual(effects, [1])

    async def test_graph_completion_receipt_is_atomic_with_its_step(self):
        from tests.llm.test_extension_contracts import GraphBoundaryTests
        from llm.components.workflows import WorkflowGraph
        effects = []
        async def work(node):
            effects.append(1)
            return {"value": "done"}
        graph = WorkflowGraph(entry="work").node("work", "custom").node("end", "end").connect("work", "end")
        app, _, session = await GraphBoundaryTests.setup_graph(self, graph.to_dict(), {"custom": work})
        complete = app.step_manager.complete
        def fail(step):
            if step.kind == "graph_node" and step.name == "work":
                raise OSError("step completion failed")
            return complete(step)
        with patch.object(app.step_manager, "complete", side_effect=fail):
            run = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(run.data.status, "failed")
        snapshot = app.run_repository.checkpoint(run.data, "graph")
        self.assertEqual(snapshot["records"]['["work"]']["status"], "started")
        with self.assertRaises(Exception):
            await session.run.resume(run.id, engine="graph")
        self.assertEqual(effects, [1])

    async def test_checkpoint_observer_reads_committed_tool_result(self):
        from tests.llm.test_long_running import LongRunningTests
        async def act(arguments):
            return "completed result"
        app, session, _ = await LongRunningTests.setup_app(self, act, [
            [chunk(calls=[call("{}", name="act")], finish="tool_calls")],
            [chunk("done", finish="stop")]])
        observed = []
        def observe(run, event):
            if event.type != EngineEventType.CHECKPOINT:
                return
            value = event.metadata.get("value", {})
            if value.get("status") == "completed" and "step_id" in value:
                step = app.step_manager.load(run, value["step_id"])
                observed.append((step.status, step.output.data))
        app._manager(session._snapshot).subscriptions.subscribe(observe, channel="engine")
        run = await (await session.run.submit("go", engine="loop")).wait()
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(observed, [("completed", "completed result")])
