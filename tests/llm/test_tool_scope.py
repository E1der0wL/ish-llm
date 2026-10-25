"""Run-bound Tool execution must retain admitted policy and execution state."""

import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from llm.components.tools import Tool, ToolRegistry
from llm.core.models import RunStatus
from llm.core.results import EngineOutput
from llm.engines.agents import AgentExecution
from llm.engines.agent_resources import AgentResourceResolver
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType
from llm.engines.graph.tool import ToolNode
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.services.runtime.tools import (
    ToolApprovalRequired, ToolExecutionScope, ToolExecutor, ToolInvocationError, ToolRuntime,
)
from tests.llm.support.continuation import resume_record
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT


class ScopeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def context(self, *, scope=None, checkpoint=None, decisions=None):
        return EngineContext(
            SimpleNamespace(id="p"), SimpleNamespace(id="s"),
            SimpleNamespace(id="r", input_message_id="m", metadata={
                "resume": resume_record(decisions=decisions or {})}),
            (), tool_scope=scope, checkpoint=checkpoint,
        )

    async def collect(self, tool, *, context=None, events=None, **kwargs):
        result = {}
        async for event in ToolExecutor().execute(tool, {}, result=result, context=context, **kwargs):
            if events is not None:
                events.append(event)
        return result

    async def test_missing_or_invalid_run_scope_fails_before_binding_classification_and_effect(self):
        handler = AsyncMock(return_value=None)
        handler.bind_arguments = Mock(side_effect=AssertionError("binding must not run"))
        tool = Tool("act", "fixture", {"type": "object"}, handler, contract=TEST_CONTRACT)
        for context in (self.context(), SimpleNamespace(run=SimpleNamespace(id="r")),
                        self.context(scope={}), self.context(scope=object())):
            with self.subTest(context=context):
                events = []
                with self.assertRaisesRegex(ToolInvocationError, "requires an inherited ToolExecutionScope"):
                    await self.collect(tool, context=context, events=events, request_key="call")
                self.assertEqual(events, [])
        handler.assert_not_awaited()
        handler.bind_arguments.assert_not_called()

    def test_context_helper_rejects_missing_scope_before_injected_executor(self):
        executor = Mock()
        with self.assertRaisesRegex(ToolInvocationError, "inherited ToolExecutionScope"):
            self.context().execute_tool(None, {}, checkpoint_key="call", result={}, executor=executor)
        executor.execute.assert_not_called()

    def test_model_and_graph_validation_do_not_publish_unconstrained_tools(self):
        context = self.context()
        with self.assertRaisesRegex(ToolInvocationError, "inherited ToolExecutionScope"):
            LoopEngine()._request(context, {})
        with self.assertRaisesRegex(ToolInvocationError, "inherited ToolExecutionScope"):
            ToolNode().validate({"tool": "act"}, context)
        with self.assertRaisesRegex(ToolInvocationError, "inherited ToolExecutionScope"):
            AgentResourceResolver.select(None, context)

    async def test_nested_agent_and_graph_tool_fail_before_emission_or_resource_lookup(self):
        node = SimpleNamespace(context=self.context(), emit=AsyncMock())
        agent = AgentExecution(engines={})
        agent.connect_resources = AsyncMock()
        for handler in (agent, ToolNode()):
            with self.subTest(handler=type(handler).__name__), self.assertRaisesRegex(
                ToolInvocationError, "inherited ToolExecutionScope"
            ):
                await handler(node)
        node.emit.assert_not_awaited()
        agent.connect_resources.assert_not_awaited()

    async def test_explicit_empty_policy_scope_retains_identity_and_counters(self):
        scope = ToolExecutionScope(ToolRuntime(), policy={})
        context = self.context(scope=scope)
        handler = AsyncMock(return_value=None)
        tool = Tool("act", "fixture", {"type": "object"}, handler, contract=TEST_CONTRACT)
        self.assertIs(ToolExecutionScope.require(context), scope)
        for key in ("first", "second"):
            await self.collect(tool, context=context, request_key=key)
        self.assertIs(context.tool_scope, scope)
        self.assertEqual(scope.completed, 2)
        self.assertEqual(handler.await_count, 2)

    async def test_context_free_executor_remains_an_independent_call(self):
        handler = AsyncMock(return_value=None)
        tool = Tool("act", "fixture", {"type": "object"}, handler, contract=TEST_CONTRACT)
        result = await self.collect(tool)
        self.assertIsNone(result["value"])
        handler.assert_awaited_once()

    async def test_approved_resume_cannot_replace_missing_scope_or_change_policy(self):
        effects = AsyncMock(return_value=None)
        classifier = Mock(return_value=None)
        from llm.components.tools import ToolClassification
        classifier.return_value = ToolClassification(category="test.write", risk_scheme="test", risk=1)
        runtime = ToolRuntime(classify=classifier)
        policy = {"max_calls": 1}
        tool = Tool("act", "fixture", {"type": "object"}, effects,
                    contract=replace(TEST_CONTRACT, approval_required=True))
        with self.assertRaises(ToolApprovalRequired) as pending:
            await self.collect(tool, context=self.context(scope=ToolExecutionScope(runtime, policy=policy)),
                               request_key="call")
        effects.assert_not_awaited()
        checkpoint = {"records": {"call": {"status": "waiting",
            "interaction": pending.exception.request.bind("custom", "call").to_dict()}}}
        resumed = self.context(scope=ToolExecutionScope(runtime, policy=policy), checkpoint=checkpoint,
                               decisions={"call": True})
        classifier.reset_mock()
        with self.assertRaisesRegex(ToolInvocationError, "inherited ToolExecutionScope"):
            await self.collect(tool, context=replace(resumed, tool_scope=None), request_key="call")
        classifier.assert_not_called()
        effects.assert_not_awaited()
        with self.assertRaisesRegex(ToolInvocationError, "policy/adapter binding changed"):
            await self.collect(tool, context=replace(resumed, tool_scope=ToolExecutionScope(runtime)),
                               request_key="call")
        effects.assert_not_awaited()
        await self.collect(tool, context=resumed, request_key="call")
        effects.assert_awaited_once()


class ScopeRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_engine_cannot_lose_project_scope_and_continue_execution(self):
        for drop_scope in (False, True):
            with self.subTest(drop_scope=drop_scope), tempfile.TemporaryDirectory() as root:
                effects = []

                async def act(arguments):
                    effects.append(arguments)

                class Custom(BaseEngine):
                    required_capabilities = ("tools",)

                    async def execute(self, context):
                        if drop_scope:
                            context = replace(context, tool_scope=None)
                        for key in ("first", "second"):
                            async for event in context.execute_tool(context.tools.get("act"), {},
                                                                   checkpoint_key=key, result={}):
                                yield event
                        yield EngineEvent(EngineEventType.OUTPUT, output=EngineOutput(text="done"))

                tools = ToolRegistry((Tool("act", "fixture", {"type": "object"}, act,
                                           contract=TEST_CONTRACT),))
                async with LargeLanguageModel(root, engines={"custom": Custom()},
                                               components=[RuntimeTools(tools)]) as app:
                    project = await app.projects.acreate(components=["tools"],
                        config={"policies": {"tools": {"max_calls": 1}}})
                    await project.components.tools.aenable("act")
                    await project.agents.acreate({"engine": "custom", "tools": ["act"]}, identifier="worker")
                    session = await project.sessions.acreate(agent_ids=["worker"])
                    run = (await (await session.run.submit("work", recipients=["worker"])).wait())[0]
                    result = await run.aresult()
                    self.assertEqual(result.status, RunStatus.FAILED)
                    self.assertEqual(result.error_code,
                                     "tool_invocation_invalid" if drop_scope else "tool_budget_exceeded")
                    self.assertEqual(len(effects), 0 if drop_scope else 1)
                    stored = await run.steps.alist()
                    if drop_scope:
                        self.assertEqual(stored, [])
