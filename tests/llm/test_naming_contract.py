"""Renamed APIs preserve the pre-cleanup configuration and durable contracts."""

import ast
from copy import deepcopy
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch
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
        self.retired = json.loads((Path(__file__).parent / 'fixtures/retired_retention_schemas.json').read_text())

    def test_config_policy_and_definition_schemas_unchanged(self):
        def baseline(schema):
            # 이름 정리 당시 fixture는 보존한다. 이후 명시적으로 추가한 Provider flag와
            # 재개 포기 보관 설명만 검증 후 정규화하고 나머지 schema를 비교한다.
            if isinstance(schema, dict):
                properties = schema.get('properties', {})
                if 'job_max_age_seconds' in properties:
                    field = properties['job_max_age_seconds']
                    self.assertEqual(field['description'],
                        '완료·취소·명시적으로 재개를 포기한 색인 작업의 입력/영수증 보관 기간. null은 무제한')
                    field['description'] = '완료·취소한 색인 작업의 입력/영수증 보관 기간. null은 무제한'
                if {'max_attempts', 'wall_timeout', 'delay_seconds', 'max_delay_seconds'} <= properties.keys():
                    for key in ('respect_retry_after', 'jitter'):
                        field = properties.pop(key)
                        self.assertEqual(set(field), {'type', 'description'})
                        self.assertEqual(field['type'], 'boolean')
                    self.assertIs(schema['additionalProperties'], False)
                for value in schema.values():
                    baseline(value)
            elif isinstance(schema, list):
                for value in schema:
                    baseline(value)
            return schema
        implementations = [RAGComponent(), MemoryComponent(), VisionComponent(), SkillComponent(),
            AgentComponent(), PromptComponent(), MCPComponent(), GoalComponent(), RefinementComponent(),
            WorkflowComponent(), LoopEngine(), GraphEngine(handlers={})]
        for implementation in implementations:
            with self.subTest(implementation=type(implementation).__name__):
                schema = implementation.describe_config()
                if isinstance(implementation, RAGComponent):
                    # H4 moves retention to Application. Verify removal, then restore only
                    # the archived schema node for this historical naming comparison.
                    policy = schema['properties']['policy']['properties']
                    self.assertNotIn('retention', policy)
                    policy['retention'] = deepcopy(self.retired['RAGComponent'])
                    misplaced = schema['properties']['config']['not']['anyOf']
                    self.assertNotIn(self.retired['RAGConfigGuard'], misplaced)
                    misplaced.append(deepcopy(self.retired['RAGConfigGuard']))
                    # Explicit cache capacities were added after the naming baseline.
                    # Validate only these additions, then compare the unchanged contract.
                    for key in ('query_cache_max_bytes', 'snapshot_cache_max_bytes'):
                        capacity = schema['properties']['config']['properties'].pop(key)
                        self.assertEqual(set(capacity), {'type', 'description', 'minimum'})
                        self.assertEqual(capacity['type'], 'integer')
                        self.assertEqual(capacity['minimum'], 0)
                if isinstance(implementation, MCPComponent):
                    # 명시적 MCP alias 선택 기능은 naming 정리 이후의 추가 계약이다.
                    tools = schema['properties']['config']['properties'].pop('tools')
                    self.assertIs(tools['additionalProperties'], False)
                    selection, = tools['patternProperties'].values()
                    self.assertEqual(set(selection['required']), {'server', 'tool'})
                    self.assertIs(selection['additionalProperties'], False)
                if isinstance(implementation, MemoryComponent):
                    # Explicit large-result processing was added after the naming
                    # baseline; validate these additions without rewriting history.
                    processing = schema['properties']['config']['properties']['processing']['properties']
                    mode = processing.pop('tool_result_mode')
                    self.assertEqual(mode, {'type': 'string', 'description': '', 'enum': ['reference', 'summary']})
                    prompt = processing.pop('tool_summary_prompt')
                    self.assertEqual(prompt, {'type': 'string', 'description': '', 'minLength': 1})
                    destination = processing.pop('tool_data')
                    self.assertIs(destination['additionalProperties'], False)
                    self.assertEqual(set(destination['required']), {'component', 'identifier'})
                    self.assertEqual(destination['properties'], {
                        'component': {'type': 'string', 'description': '', 'minLength': 1},
                        'identifier': {'type': 'string', 'description': '', 'minLength': 1}})
                self.assertEqual(digest(baseline(schema)), self.before['schemas'][type(implementation).__name__])
        policies = ProjectConfig.describe_policies()
        self.assertNotIn('retention', policies['properties'])
        policies['properties']['retention'] = deepcopy(self.retired['ProjectPolicy'])
        self.assertEqual(digest(policies), self.before['schemas']['ProjectPolicy'])

    def test_persisted_config_domain_fields_and_workflow_unchanged(self):
        config = ProjectConfig.deserialize(json.dumps(self.before['project_config']))
        self.assertEqual(config.to_dict(), self.before['project_config'])
        for domain in (Project, Session, Run, Step):
            expected = list(self.before['fields'][domain.__name__])
            if domain is Project:
                # H2 adds managed storage lifetime without renaming existing fields.
                expected.insert(expected.index('components') + 1, 'initialized_components')
            self.assertEqual([item.name for item in fields(domain)], expected)
        graph = WorkflowGraph(entry='end', metadata={'title': 'Review'}).node('end', 'end').to_dict()
        self.assertEqual(graph, self.before['workflow'])

    def test_binding_vocabulary_preserves_unchanged_fields(self):
        config = ProjectConfig(self.before['project_config'])
        scope = ToolExecutionScope(ToolRuntime(revision='host-1'), policy={'max_calls': 5})
        context = EngineContext(NS(config=config, components=()), NS(config={}), NS(engine='loop'), (), tool_scope=scope)
        self.assertEqual(scope.binding(), self.before['scope_binding'])
        loop = LoopEngine()
        loop._configure({'completion': {'model': 'test/model', 'temperature': None}, 'max_iterations': 3})
        # Provider substitution now participates in resume identity. Keep the
        # historical naming fixture intact and verify the explicitly added field.
        original_hash = hashlib.sha256
        with patch('llm.engines.loop.engine.hashlib.sha256', wraps=original_hash) as tracked:
            loop._binding(context)
        payload = json.loads(tracked.call_args.args[0])
        self.assertEqual(payload.pop('adapter'), {
            'type': 'llm.providers.litellm.LiteLLMCompletionAdapter', 'binding': None})
        self.assertEqual(digest(payload), self.before['loop_binding'])
        graph = GraphEngine(handlers={}, parameter_key='graph').for_request({'workflow': 'flow'})
        expected = dict(self.before['graph_binding'])
        # Agent catalogue identity was intentionally replaced by per-selection
        # records. Other persisted names are still protected by this fixture.
        self.assertEqual(expected.pop('agents'), [])
        self.assertEqual(graph._binding(context, self.before['workflow'], []), expected)

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
