"""Renamed APIs preserve the pre-cleanup configuration and durable contracts."""

import ast
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from llm.llm import LargeLanguageModel, BackendServices
from llm.core.models import ProjectConfig, Project, Session, Run, Step
from llm.components.tools import ToolComponent
from llm.services.runtime.tools import ToolRuntime, ToolExecutionScope
from llm.services.runtime.output import OutputBuffer
from llm.engines.base import EngineContext
from llm.engines import EngineRegistry
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowGraph, WorkflowComponent
from llm.components.rag import RAGComponent
from llm.components.memory import MemoryComponent
from llm.components.vision import VisionComponent
from llm.components.skills import SkillComponent
from llm.components.agents import AgentComponent
from llm.components.prompts import PromptComponent
from llm.components.mcp import MCPComponent
from llm.components.goals import GoalComponent
from llm.components.refinement import RefinementComponent


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class NamingContractTests(unittest.TestCase):
    def setUp(self):
        # Captured from 607310f, not regenerated from the implementation under test.
        self.before = json.loads((Path(__file__).parent / 'fixtures/naming_contract.json').read_text())

    def test_config_policy_and_definition_schemas_unchanged(self):
        implementations = [RAGComponent(), MemoryComponent(), VisionComponent(), SkillComponent(),
            AgentComponent(), PromptComponent(), MCPComponent(), GoalComponent(), RefinementComponent(),
            WorkflowComponent(), LoopEngine(), GraphEngine(handlers={})]
        for implementation in implementations:
            with self.subTest(implementation=type(implementation).__name__):
                self.assertEqual(digest(implementation.describe_config()), self.before['schemas'][type(implementation).__name__])
        self.assertEqual(digest(ProjectConfig.describe_policies()), self.before['schemas']['ProjectPolicy'])

    def test_persisted_config_domain_fields_and_workflow_unchanged(self):
        config = ProjectConfig.deserialize(json.dumps(self.before['project_config']))
        self.assertEqual(config.to_dict(), self.before['project_config'])
        for domain in (Project, Session, Run, Step):
            self.assertEqual([item.name for item in fields(domain)], self.before['fields'][domain.__name__])
        graph = WorkflowGraph(entry='end', metadata={'title': 'Review'}).node('end', 'end').to_dict()
        self.assertEqual(graph, self.before['workflow'])

    def test_old_tool_loop_and_graph_bindings_still_match(self):
        config = ProjectConfig(self.before['project_config'])
        scope = ToolExecutionScope(ToolRuntime(revision='host-1'), policy={'max_calls': 5})
        context = EngineContext(NS(config=config, components=()), NS(config={}), NS(engine='loop'), (), tool_scope=scope)
        self.assertEqual(scope.binding(), self.before['scope_binding'])
        loop = LoopEngine()
        loop._configure({'completion': {'model': 'test/model', 'temperature': None}, 'max_iterations': 3})
        self.assertEqual(loop._binding(context), self.before['loop_binding'])
        graph = GraphEngine(handlers={}, parameter_key='graph').for_request({'workflow': 'flow'})
        self.assertEqual(graph._binding(context, self.before['workflow'], []), self.before['graph_binding'])

    def test_parameter_key_only_selects_the_explicit_engine_path(self):
        engine = LoopEngine(parameter_key='shared')
        registry = EngineRegistry()
        registry.register('chat', engine)
        self.assertIs(registry.get('chat'), engine)
        config = ProjectConfig(parameters={'engines': {'shared': {'config': {'system_prompt': None}}}})
        self.assertEqual(engine.describe_config()['x-parameter-key'], 'shared')
        view = engine.resolve_config(config, 'chat')
        self.assertIsNone(view['values']['config']['system_prompt'])
        self.assertEqual(view['sources']['/config/system_prompt'], 'project')
        self.assertEqual(view['configuration_key'], 'shared')

    def test_removed_symbols_have_no_production_aliases(self):
        retired = {'SettingsLayout', 'settings_name', 'settings_layout', 'ServiceConfig',
                   'ToolPolicy', 'tool_policy', 'OutputPolicy', 'output_policy', 'LogSettings',
                   'validate_settings', 'from_settings', 'configuration_schema', 'effective_configuration',
                   'settings'}
        for path in (Path(__file__).parents[2] / 'llm').rglob('*.py'):
            with self.subTest(path=path):
                for node in ast.walk(ast.parse(path.read_text())):
                    symbol = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                              else node.arg if isinstance(node, ast.arg) else node.name
                              if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else None)
                    self.assertNotIn(symbol, retired)


class ConfigFacadeTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_resolve_describe_config_have_distinct_roles(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[ToolComponent()], engines={'loop': LoopEngine()},
                    services=BackendServices(tool_runtime=ToolRuntime(), output_buffer=OutputBuffer())) as app:
                project = await app.projects.acreate('Naming', components=['tools'], config=ProjectConfig(parameters={
                    'engines': {'loop': {'config': {'system_prompt': 'project'}}}}))
                tools = await project.components.aget('tools')
                self.assertEqual(await tools.aget_config(), {})
                await tools.aconfigure({'config': {'enabled': []}})
                raw = await tools.aget_config()
                effective = await tools.aresolve_config()
                self.assertEqual(effective['values'], raw)
                self.assertEqual(effective['sources']['/config/enabled'], 'project')
                view = await project.adescribe_config()
                self.assertEqual(view['components']['tools']['configuration'], raw)
                self.assertIn('schema', view)
                self.assertEqual((await project.avalidate_config(view['project']['config']))['config_version'], view['config_version'])
                session = await project.sessions.acreate(config={'parameters': {'engines': {'loop': {'config': {'system_prompt': None}}}}})
                session_view = await session.adescribe_config()
                self.assertIsNone(session_view['effective_engines']['loop']['values']['config']['system_prompt'])
                self.assertEqual(session_view['effective_engines']['loop']['sources']['/config/system_prompt'], 'session')
                self.assertIn('tool_runtime', app.describe_host()['values'])
                self.assertIn('output_buffer', app.describe_host()['values'])
