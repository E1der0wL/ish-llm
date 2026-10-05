"""Host 인자 제약의 schema/runtime/승인 binding을 공통 경계에서 검증한다."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jsonschema import Draft202012Validator, ValidationError
from llm.components.tools import Tool, ToolRegistry
from llm.components.tools.constraints import constrained_parameters
from llm.components.tools.builtin import BuiltinTools
from llm.components.rag.tools import search_tools
from llm.components.memory.tools import memory_tools
from llm.services.runtime.tools import ToolPolicy, ToolExecutionScope, ToolExecutor, ToolApprovalRequired, ToolInvocationError
from llm.services.configuration import ServiceConfig
from llm.engines.base import EngineContext
from llm.engines.loop import LoopEngine
from llm.engines.graph.tool import ToolNode
from llm.llm import LargeLanguageModel
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm import test_nested_graph as nested


SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1},
    "method": {"type": "string", "enum": ["hybrid", "vector", "bm25"]},
    "max_hops": {"type": "integer", "minimum": 1}}, "required": ["query"], "additionalProperties": False}
FIELDS = {"limit": {"mode": "bounded", "minimum": 1, "maximum": 20},
          "method": {"mode": "selectable", "values": ["hybrid", "vector"]},
          "max_hops": {"mode": "fixed", "value": 2}}


class ConstraintSchemaTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry([Tool("rag_search", "test", deepcopy(SCHEMA), AsyncMock())])

    def test_modes_preserve_original_and_reject_wrong_values(self):
        before = self.registry.definitions()
        policy = ToolPolicy(argument_constraints={"rag_search": FIELDS})
        constraints = policy.argument_constraints
        effective = self.registry.definitions(constraints=constraints)[0]["function"]["parameters"]
        for values in ({"query": "q"}, {"query": "q", "max_hops": 2},
                       {"query": "q", "limit": 1, "method": "hybrid"},
                       {"query": "q", "limit": 20, "method": "vector"}):
            _, result = self.registry.prepare("rag_search", json.dumps(values), constraints=constraints)
            self.assertEqual(result["max_hops"], 2)
            Draft202012Validator(effective).validate(result)
        for values in ({"max_hops": 3}, {"limit": 21}, {"limit": 0}, {"limit": True},
                       {"method": "bm25"}, {"mode": "fixed"}, {"limit": None}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.registry.prepare("rag_search", json.dumps({"query": "q", **values}), constraints=constraints)
        self.assertEqual(before, self.registry.definitions())
        self.registry.prepare("rag_search", '{"query":"q","limit":1000000000}')

    def test_original_refs_and_bounds_remain_the_upper_contract(self):
        schema = {"type": "object", "$defs": {"value": {"type": "integer", "minimum": 3, "maximum": 9}},
                  "properties": {"value": {"$ref": "#/$defs/value"}}}
        for rule in ({"mode": "fixed", "value": 2}, {"mode": "selectable", "values": [3, 10]}):
            with self.assertRaises(ValidationError):
                constrained_parameters(schema, {"value": rule})
        effective = constrained_parameters(schema, {"value": {"mode": "bounded", "maximum": 100}})
        self.assertTrue(Draft202012Validator(effective).is_valid({"value": 5}))
        self.assertFalse(Draft202012Validator(effective).is_valid({"value": 10}))
        self.assertTrue(Draft202012Validator(constrained_parameters(schema, {
            "value": {"mode": "fixed", "value": 5}})).is_valid({"value": 5}))

    def test_child_narrows_never_widens_and_policy_is_detached(self):
        source = {"rag_search": deepcopy(FIELDS)}
        scope = ToolExecutionScope(ToolPolicy(argument_constraints=source))
        source["rag_search"]["max_hops"]["value"] = 9
        child = scope.child(allowed_tools=["rag_search"], argument_constraints={"rag_search": {
            "limit": {"mode": "bounded", "maximum": 10}, "method": {"mode": "selectable", "values": ["vector"]}}})
        self.assertEqual(child.binding()["argument_constraints"]["rag_search"]["limit"]["minimum"], 1)
        self.assertEqual(child.binding()["argument_constraints"]["rag_search"]["max_hops"]["value"], 2)
        for key, rule in (("limit", {"mode": "bounded", "maximum": 30}),
                          ("method", {"mode": "selectable", "values": ["bm25"]}),
                          ("max_hops", {"mode": "fixed", "value": 3})):
            with self.assertRaises(ValueError):
                scope.child(allowed_tools=["rag_search"], argument_constraints={"rag_search": {key: rule}})
        for invalid in ([], {"rag_search": {"limit": {"mode": "bounded", "maximum": float("inf")}}},
                        {"rag_search": {"limit": {"mode": "bounded", "minimum": 3, "maximum": 1}}}):
            with self.assertRaises((ValueError, TypeError)):
                ToolPolicy(argument_constraints=invalid)
        with self.assertRaises(ValueError):
            ToolExecutionScope(ToolPolicy(argument_constraints={"rag_search": {
                "limit": {"mode": "bounded", "maximum": 30}}}), parent=scope)


class ConstraintRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def context(self, policy, *, checkpoint=None, decisions=None):
        return EngineContext(SimpleNamespace(id="p"), SimpleNamespace(id="s"),
            SimpleNamespace(id="r", input_message_id="m", metadata={"resume": {"decisions": decisions or {}}}),
            (), tool_scope=ToolExecutionScope(policy), checkpoint=checkpoint)

    async def execute(self, tool, values, context, **kwargs):
        result = {}
        async for _ in ToolExecutor().execute(tool, values, result=result, context=context, **kwargs):
            pass
        return result

    async def test_direct_execution_revalidates_before_authorization_or_effect(self):
        handler, authorize = AsyncMock(return_value=None), AsyncMock(return_value=True)
        tool = Tool("rag_search", "test", SCHEMA, handler)
        context = self.context(ToolPolicy(authorize=authorize, argument_constraints={"rag_search": FIELDS}))
        with self.assertRaises(ValidationError):
            await self.execute(tool, {"query": "q", "max_hops": 3}, context)
        handler.assert_not_awaited()
        authorize.assert_not_awaited()
        await self.execute(tool, {"query": "q"}, context)
        self.assertEqual(handler.await_args.args[0], {"query": "q", "max_hops": 2})
        self.assertEqual(authorize.await_args.args[0].arguments, {"query": "q", "max_hops": 2})

    async def test_durable_approval_binding_rejects_changed_constraints(self):
        async def ask(call):
            raise ToolApprovalRequired()
        handler = AsyncMock(return_value=None)
        tool = Tool("rag_search", "test", SCHEMA, handler)
        policy = ToolPolicy(authorize=ask, argument_constraints={"rag_search": FIELDS})
        with self.assertRaises(ToolApprovalRequired) as pending:
            await self.execute(tool, {"query": "q"}, self.context(policy), request_key="tool-1")
        saved = {"records": {"tool-1": {"status": "waiting", "interaction": pending.exception.request.bind("custom", "tool-1").to_dict()}}}
        changed = deepcopy(FIELDS)
        changed["limit"]["maximum"] = 10  # supplied arguments still valid; binding itself must reject.
        with self.assertRaisesRegex(ToolInvocationError, "constraints changed"):
            await self.execute(tool, {"query": "q"}, self.context(ToolPolicy(authorize=ask,
                argument_constraints={"rag_search": changed}), checkpoint=saved, decisions={"tool-1": True}), request_key="tool-1")
        handler.assert_not_awaited()
        await self.execute(tool, {"query": "q"}, self.context(policy, checkpoint=saved,
            decisions={"tool-1": True}), request_key="tool-1")
        handler.assert_awaited_once()

    async def test_real_component_and_builtin_schemas_share_runtime_enforcement(self):
        rag = SimpleNamespace(name="rag", effective_configuration=lambda: {"values": {"config": {"search": {
            "method": "hybrid", "expand": "section", "limit": 5, "max_hops": 2, "relation_limit": 30}}}},
            has_reranker=lambda: True, asearch=AsyncMock(return_value={"documents": []}))
        memory = SimpleNamespace(project=SimpleNamespace(id="p"), history_reader=None,
                                 asearch=AsyncMock(return_value=[]))
        for registry, name, fields, args, invalid in (
            (search_tools(rag), "rag_search", FIELDS, {"query": "q"}, {"query": "q", "method": "bm25"}),
            (memory_tools(memory), "memory_search", {"status": {"mode": "fixed", "value": "confirmed"}},
             {"query": "q"}, {"query": "q", "status": "all"})):
            with self.subTest(name=name):
                context = self.context(ToolPolicy(argument_constraints={name: fields}))
                await self.execute(registry.get(name), args, context)
                with self.assertRaises(ValidationError):
                    await self.execute(registry.get(name), invalid, context)
        self.assertEqual(rag.asearch.await_args.kwargs["max_hops"], 2)
        self.assertEqual(memory.asearch.await_args.kwargs["status"], "confirmed")
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sample").write_text("one\ntwo\nthree\n")
            tools = BuiltinTools(root)
            self.addAsyncCleanup(tools.close)
            context = self.context(ToolPolicy(argument_constraints={"file_read": {"max_lines": {"mode": "fixed", "value": 1}}}))
            result = await self.execute(tools.registry.get("file_read"), {"path": "sample"}, context)
            self.assertNotIn("two", json.dumps(result))
            with self.assertRaises(ValidationError):
                await self.execute(tools.registry.get("file_read"), {"path": "sample", "max_lines": 2}, context)

    async def test_loop_model_schema_and_actual_arguments_agree(self):
        with tempfile.TemporaryDirectory() as root:
            handler = AsyncMock(return_value="found")
            registry = ToolRegistry([Tool("rag_search", "test", SCHEMA, handler)])
            model = ScriptedCompletion([chunk(calls=[call('{"query":"q"}', name="rag_search")], finish="tool_calls")],
                                       [chunk("done", finish="stop")])
            async with LargeLanguageModel(root, components=[RuntimeTools(registry)],
                    engines={"loop": LoopEngine(completion_fn=model, completion_kwargs={"model": "test"})},
                    services=ServiceConfig(tool_policy=ToolPolicy(argument_constraints={"rag_search": FIELDS}))) as app:
                project = await app.projects.acreate("work", components=["tools"])
                await project.components.tools.aenable("rag_search")
                session = await project.sessions.acreate()
                run = await (await session.run.submit("find", engine="loop")).wait()
                self.assertEqual(str(run.data.status), "completed", run.data.error)
                schema = model.requests[0]["tools"][0]["function"]["parameters"]
                Draft202012Validator(schema).validate(handler.await_args.args[0])
                self.assertFalse(Draft202012Validator(schema).is_valid({"query": "q", "max_hops": 9}))


class GraphConstraintTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = nested.NestedGraphTests.asyncSetUp
    setup = nested.NestedGraphTests.setup
    request = nested.NestedGraphTests.request

    async def test_graph_tool_node_uses_host_constraints(self):
        self.tools = ToolRegistry([Tool("effect", "test", SCHEMA, AsyncMock(return_value="found"))])
        await self.setup({"main": nested.action_graph("tool", tool="effect", arguments={"query": "q"})},
                         handlers={"tool": ToolNode()}, services=ServiceConfig(tool_policy=ToolPolicy(argument_constraints={"effect": FIELDS})))
        run = await self.request()
        self.assertEqual(str(run.data.status), "completed", run.data.error)
        self.assertEqual(self.tools.get("effect").handler.await_args.args[0]["max_hops"], 2)

    async def test_reopen_with_changed_host_constraint_rejects_graph_resume(self):
        async def ask(call):
            raise ToolApprovalRequired()
        handler = AsyncMock(return_value="found")
        self.tools = ToolRegistry([Tool("effect", "test", SCHEMA, handler)])
        await self.setup({"main": nested.action_graph("tool", tool="effect", arguments={"query": "q"})},
            handlers={"tool": ToolNode()}, services=ServiceConfig(tool_policy=ToolPolicy(
                authorize=ask, argument_constraints={"effect": FIELDS})))
        paused = await self.request()
        self.assertEqual(str(paused.data.status), "paused", paused.data.error)
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond("approve"))
        await self.app.shutdown()
        changed = deepcopy(FIELDS)
        changed["limit"]["maximum"] = 10
        async with LargeLanguageModel(self.root, engines={"graph": self.engine}, components=[
                nested.WorkflowComponent(), nested.AgentComponent(), RuntimeTools(self.tools), nested.SkillComponent()],
                services=ServiceConfig(tool_policy=ToolPolicy(authorize=ask, argument_constraints={"effect": changed}))) as app:
            project = await app.projects.aload(self.project.id)
            session = await project.sessions.aload(self.session.id)
            with self.assertRaises(nested.RunRequestError):
                await session.run.resume(paused.id, engine="graph")
        handler.assert_not_awaited()
