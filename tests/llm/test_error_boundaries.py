"""분류된 오류 보존·미분류 fallback·Step 저장·중첩 실행 경계를 검증한다."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from llm.core.contracts import Diagnostic, ResourceRef
from llm.errors import CodedError, exception_chain, stable_error_code
from llm.core.models import RunStatus, StepStatus, new_id
from llm.components.agents import AgentComponent
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.tools import Tool
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from llm.engines.pipeline import PipelineEngine, PreparationStep
from llm.llm import LargeLanguageModel
from llm.providers.requests import ProviderError
from llm.providers.calls import ProviderCapacityError
from llm.policies import ExecutionLimitError
from llm.services.runtime.tools import ToolExecutor, ToolExecutionError


class UntrustedError(Exception):
    # 관찰용 Diagnostic은 기존처럼 보존하지만 이 속성만으로 Run code를 신뢰하지 않는다.
    code = "provider_rate_limit"


def failing_engine(error):
    async def fail(context):
        raise error
        yield  # async generator 계약
    return BaseEngine(action=fail)


class FailureContractTests(unittest.TestCase):
    def test_stable_codes_require_explicit_contract(self):
        class ExtensionError(CodedError, ValueError):
            code = "extension_known"
        for error, expected in ((ProviderError("provider_rate_limit"), "provider_rate_limit"),
                (ExecutionLimitError("run_timeout", "deadline"), "run_timeout"),
                (ProviderCapacityError("busy"), "provider_capacity"),
                (ToolExecutionError("failed"), "tool_failed"), (ExtensionError(), "extension_known"),
                (UntrustedError(), None), (RuntimeError("unexpected"), None)):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(stable_error_code(error), expected)
        for invalid in (None, "", "  ", 429, {}):
            error = ProviderError("initial")
            error.code = invalid
            self.assertIsNone(stable_error_code(error))

    def test_cause_context_precedence_suppression_cycles_and_bound(self):
        known = ProviderError("provider_timeout")
        wrapper = RuntimeError("wrapper")
        wrapper.__context__ = known
        self.assertEqual(stable_error_code(wrapper), "provider_timeout")
        wrapper.__cause__ = ProviderError("provider_rate_limit")
        self.assertEqual(stable_error_code(wrapper), "provider_rate_limit")
        outer = ExecutionLimitError("tool_timeout", "outer")
        outer.__cause__ = known
        self.assertEqual(stable_error_code(outer), "tool_timeout")
        wrapper.__cause__ = None
        wrapper.__suppress_context__ = True
        self.assertIsNone(stable_error_code(wrapper))
        wrapper.__cause__ = wrapper
        self.assertIsNone(stable_error_code(wrapper))
        chain = RuntimeError("deep")
        current = chain
        for _ in range(40):
            current.__cause__ = RuntimeError("next")
            current = current.__cause__
        current.__cause__ = known
        self.assertEqual(len(list(exception_chain(chain))), 32)
        self.assertIsNone(stable_error_code(chain))

    def test_failure_helper_preserves_diagnostic_and_copies_metadata(self):
        source = ResourceRef("component", "retrieval")
        supplied = Diagnostic("custom_known_error", "details", source=source, details={"attempt": 2})
        metadata = {"node": {"name": "retrieve"}}
        event = BaseEngine.step_failed_event("s", RuntimeError("raw"), message="display",
            diagnostic=supplied, metadata=metadata)
        metadata["node"]["name"] = "changed"
        self.assertEqual(event.diagnostic, supplied)
        self.assertEqual(event.error, "display")
        self.assertEqual(event.metadata["node"]["name"], "retrieve")
        self.assertIsNone(event.failure_code)
        generic = BaseEngine.step_failed_event("s", RuntimeError("unknown"))
        self.assertEqual(generic.diagnostic.code, "step_failed")
        self.assertEqual(generic.diagnostic.source.step_id, "s")
        untrusted = BaseEngine.step_failed_event("s", UntrustedError("observed"))
        self.assertEqual(untrusted.diagnostic.code, "provider_rate_limit")
        self.assertIsNone(untrusted.failure_code)
        wrapped = RuntimeError("wrapped")
        wrapped.__cause__ = ProviderError("provider_rate_limit")
        self.assertEqual(BaseEngine.step_failed_event("s", wrapped).failure_code, "provider_rate_limit")


class FailureRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, engine, *, components=(), definitions=None, config=None, engine_options=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        backend = LargeLanguageModel(temporary.name, engines={"test": engine}, components=list(components))
        self.addAsyncCleanup(backend.shutdown)
        project = await backend.projects.acreate(config=config or {}, components=[c.name for c in components])
        for component, records in (definitions or {}).items():
            data = await project.components.aget(component)
            for name, value in records.items():
                await data.acreate(value, identifier=name)
        session = await project.sessions.acreate()
        handle = await (await session.run.submit("request", engine="test", engine_options=engine_options or {})).wait(timeout=15)
        result = await handle.aresult()
        steps = await handle.steps.alist()
        # Run 공개 투영과 디스크가 같은 결과를 보존한다.
        self.assertEqual(json.loads((handle.data.paths.root / "run.json").read_text())["error_code"], result.error_code)
        return result, steps

    async def test_graph_coded_and_untrusted_failures(self):
        for failure, expected in ((ProviderError("provider_rate_limit"), "provider_rate_limit"),
                                  (UntrustedError("fake"), "engine_failed")):
            async def fail(node):
                raise failure
            graph = WorkflowGraph(entry="fail").node("fail", "fail").node("end", "end").connect("fail", "end").to_dict()
            result, steps = await self.execute(GraphEngine(handlers={"fail": fail}),
                engine_options={"workflow": "flow"}, components=[WorkflowComponent()], definitions={"workflows": {"flow": graph}})
            self.assertEqual(result.status, RunStatus.FAILED)
            self.assertEqual(result.error_code, expected)
            failed = [s for s in steps if s.status == StepStatus.FAILED]
            self.assertEqual({s.kind for s in failed}, {"graph", "graph_node"})
            self.assertTrue(all(s.diagnostic.code == "provider_rate_limit" for s in failed))
            self.assertTrue(all("failure_code" not in json.loads((s.paths.root / "step.json").read_text()) for s in steps))

    async def test_agent_and_nested_graph_failures(self):
        class AgentEngine(BaseEngine):
            def for_agent(self, definition):
                return self
        async def fail(context):
            raise ProviderError("provider_timeout")
            yield
        async def node_fail(node):
            raise ProviderError("provider_timeout")
        inner = WorkflowGraph(entry="fail").node("fail", "fail").node("end", "end").connect("fail", "end").to_dict()
        outer = WorkflowGraph(entry="agent").node("agent", "agent", agent="worker").node("end", "end").connect("agent", "end").to_dict()
        for child in (AgentEngine(action=fail), GraphEngine(handlers={"fail": node_fail})):
            result, steps = await self.execute(GraphEngine(handlers={"agent": AgentNode(engines={"child": child})}),
                engine_options={"workflow": "outer"}, components=[WorkflowComponent(), AgentComponent()], definitions={
                    "workflows": {"outer": outer, "inner": inner}, "agents": {"worker": {"engine": "child", "purpose": "Test failures",
                    "engine_options": {"workflow": "inner"} if isinstance(child, GraphEngine) else {}}}})
            self.assertEqual(result.error_code, "provider_timeout")
            self.assertTrue(any(s.kind == "agent" and s.status == StepStatus.FAILED for s in steps))
            self.assertTrue(all(s.diagnostic.code == "provider_timeout" for s in steps if s.status == StepStatus.FAILED))

    async def test_pipeline_stops_and_preserves_only_classified_failure(self):
        for failure, expected in ((ProviderError("provider_rate_limit"), "provider_rate_limit"),
                                  (UntrustedError("sdk"), "engine_failed")):
            following = []
            async def next_stage(context):
                following.append(True)
            result, steps = await self.execute(PipelineEngine([failing_engine(failure), PreparationStep("next", next_stage)]))
            self.assertEqual(result.error_code, expected)
            self.assertEqual(following, [])
            self.assertEqual(len([s for s in steps if s.status == StepStatus.FAILED]), 2)

    async def test_unknown_and_wrapped_known_run_codes(self):
        wrapped = RuntimeError("outer")
        wrapped.__cause__ = ProviderError("provider_rate_limit")
        for failure, expected in ((wrapped, "provider_rate_limit"), (RuntimeError("unexpected"), "engine_failed"),
                                  (UntrustedError("vendor"), "engine_failed"), (ProviderCapacityError("busy"), "provider_capacity")):
            result, steps = await self.execute(failing_engine(failure))
            self.assertEqual(result.status, RunStatus.FAILED)
            self.assertEqual(result.error_code, expected)
            if type(failure) is RuntimeError and failure.__cause__ is None:
                self.assertEqual(steps[0].diagnostic.code, "step_failed")

    async def test_step_persistence_does_not_reclassify_explicit_diagnostic(self):
        class Engine:
            async def execute(self, context):
                step_id = new_id()
                yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id)
                yield BaseEngine.step_failed_event(step_id, RuntimeError("raw"),
                    diagnostic=Diagnostic("custom_known_error", "explicit", details={"reason": "known"}))
                raise RuntimeError("raw")
        result, steps = await self.execute(Engine())
        self.assertEqual(result.error_code, "engine_failed")
        self.assertEqual(steps[0].diagnostic.code, "custom_known_error")
        self.assertEqual(steps[0].diagnostic.details, {"reason": "known"})
        self.assertEqual(steps[0].error, "raw")

    async def test_tool_diagnostic_effect_and_timeout_are_unchanged(self):
        async def failed(args):
            raise ToolExecutionError("no effect", effect="none", retryable=True)
        events = []
        with self.assertRaises(ToolExecutionError):
            async for event in ToolExecutor().execute(Tool("fail", "fail", {"type": "object"}, failed), {}, result={}):
                events.append(event)
        diagnostic = events[-1].diagnostic
        self.assertEqual(diagnostic.code, "tool_failed")
        self.assertEqual(diagnostic.details, {"effect": "none", "retryable": True})
        async def slow(args):
            await asyncio.Event().wait()
        events = []
        with self.assertRaises(ExecutionLimitError):
            async for event in ToolExecutor(timeout_seconds=.01).execute(Tool("slow", "slow", {"type": "object"}, slow), {}, result={}):
                events.append(event)
        self.assertEqual(events[-1].diagnostic.code, "tool_timeout")

    async def test_run_timeout_and_cancellation(self):
        async def wait(context):
            await asyncio.Event().wait()
            yield
        result, _ = await self.execute(BaseEngine(action=wait), config={"policies": {"run": {"timeout_seconds": .05}}})
        self.assertEqual(result.error_code, "run_timeout")
        async def cancel(context):
            raise asyncio.CancelledError()
            yield
        result, _ = await self.execute(BaseEngine(action=cancel))
        self.assertEqual(result.status, RunStatus.INTERRUPTED)
        self.assertEqual(result.error_code, "interrupted")
