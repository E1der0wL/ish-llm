"""Graph Agent는 별도 Workflow 환경이며 behavior나 두 번째 Tool 권한 계층이 아니다."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llm.core.models import ProjectConfig
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from tests.llm import test_nested_graph as nested


class GraphAgentContractTests(unittest.TestCase):
    def test_rejects_behavior_by_presence_before_resource_lookup(self):
        engine = GraphEngine(handlers={})
        agent = AgentNode(engines={"orchestrator": engine})
        base = {"purpose": "Review workflow", "engine": "orchestrator", "engine_options": {"workflow": "flow"}}
        invalid = {"completion": {}, "system_prompt": None, "tools": [], "resources": {}, "policy": {},
                   "input_schema": {}, "output_schema": {}, "output_format": "json"}
        for key, value in invalid.items():
            with self.subTest(field=key), self.assertRaises(ValueError):
                engine.for_agent({**base, key: value})
        for resources in ({}, {"prompt": "missing"}, {"skills": ["missing"]}, {"rag": True}, {"mcp": {"missing": {}}}):
            context = SimpleNamespace(capabilities={"agents": [{"records": {"review": {**base, "resources": resources}}}]})
            with patch.object(agent, "_resources", side_effect=AssertionError("resource lookup happened")):
                with self.assertRaises(ValueError):
                    agent.validate({"agent": "review"}, context)

    def test_only_graph_settings_and_node_data_contracts(self):
        engine = GraphEngine(handlers={})
        options = {"workflow": "flow", "config": {"buffer_size": 2, "cleanup_timeout": .1},
                   "policy": {"max_steps": 20, "max_parallelism": 2, "timeout_seconds": 3, "max_nested_depth": 100}}
        worker = engine.for_agent({"purpose": "Review", "engine": "review", "engine_options": options})
        self.assertEqual(worker.resolve_config(ProjectConfig(), "review")["values"], {k: v for k, v in options.items() if k != "workflow"})
        profile = {"purpose": "Review", "engine": "review", "engine_options": {"workflow": "flow"}}
        handler = AgentNode(engines={"review": engine})
        context = SimpleNamespace(capabilities={"agents": [{"records": {"review": profile}}]})
        handler.validate({"agent": "review", "input_schema": {}, "output_schema": {}, "inputs": {}, "outputs": {}}, context)
        for key, value in (("emit_text", False), ("output_format", "json")):
            with self.assertRaises(ValueError):
                handler.validate({"agent": "review", key: value}, context)
        self.assertIsNone(engine.cleanup_timeout)
        self.assertNotIn("enforced", engine.resolve_config(ProjectConfig(), "graph"))
        self.assertIsNone(GraphEngine(handlers={}).max_nested_depth)
        self.assertEqual(GraphEngine(handlers={}).resolve_config(ProjectConfig(parameters={"engines": {
            "graph": {"policy": {"max_nested_depth": 100}}}}), "graph")["values"]["policy"]["max_nested_depth"], 100)


class GraphEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = nested.NestedGraphTests.asyncSetUp
    setup = nested.NestedGraphTests.setup
    request = nested.NestedGraphTests.request

    async def test_graph_agent_uses_distinct_handlers_and_preserves_parent_scope(self):
        seen = []
        async def review(node):
            seen.append(node.context.tool_scope)
            return {"answer": "checked"}
        child = GraphEngine(handlers={"review": review})
        handler = AgentNode(engines={"review_environment": child})
        main = nested.action_graph("agent", agent="review",
            inputs={"query": "/request"}, input_schema={"type": "object", "required": ["query"]},
            output_schema={"type": "object", "required": ["data"]})
        main["initial_state"] = {"request": "review request"}
        await self.setup({"main": main,
            "child": nested.action_graph("review")}, handlers={"agent": handler})
        await self.agents.acreate({"purpose": "Review", "engine": "review_environment",
                                  "engine_options": {"workflow": "child"}}, identifier="review")
        run = await self.request()
        self.assertEqual(str(run.data.status), "completed", run.data.error)
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0].parent)
        step = next(s for s in await run.steps.alist() if s.kind == "agent")
        self.assertNotIn("resources", step.metadata)
        self.assertEqual(step.metadata["workflow_id"], "child")
