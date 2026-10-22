"""Current owner schemas and durable naming contracts are explicit."""

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

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

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
from llm.core.agents import AgentDefinition
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

    def test_current_implementation_fields_are_closed_and_owned(self):
        # Explicit current vocabulary, reviewed against each owner. The archived
        # naming digests are historical evidence, not a schema conversion target.
        cases = (
            (RAGComponent(),
             'chunk_size document_kwargs embedding_cache_max_bytes embedding_params extraction extraction_batch_size extraction_params graph index_batch_size query_cache_max_bytes query_kwargs rerank_params search search_cache_chars snapshot_cache_max_bytes',
             'embedding_concurrency extraction ingestion provider'),
            (MemoryComponent(), 'cache_records min_score processing search_limit search_status search_strategy',
             'max_search_results processing tool_write_status'),
            (VisionComponent(), 'completion ocr', 'limits ocr provider'),
            (SkillComponent(), '', ''), (PromptComponent(), '', ''),
            (MCPComponent(), 'tools', ''),
            (GoalComponent(), 'identifiers nested_agent_ids priority', 'inject write_tools'),
            (RefinementComponent(), '', 'apply_tools proposal_operations require_evaluation'),
            (WorkflowComponent(), '', ''),
            (LoopEngine(), 'buffer_size completion system_prompt',
             'completion max_argument_chars max_iterations max_output_chars max_tool_calls provider request_timeout tool_timeout'),
            (GraphEngine(handlers={}), 'buffer_size cleanup_timeout',
             'max_nested_depth max_parallelism max_steps timeout_seconds'),
        )
        self.assertEqual(AgentDefinition.schema['required'], ['engine'])
        self.assertFalse(AgentDefinition.schema['additionalProperties'])
        self.assertNotIn('config', AgentDefinition.schema['properties'])
        for implementation, config_fields, policy_fields in cases:
            with self.subTest(implementation=type(implementation).__name__):
                schema = implementation.describe_config()
                Draft202012Validator.check_schema(schema)
                self.assertEqual(set(schema['properties']), {'config', 'policy'})
                self.assertIs(schema['additionalProperties'], False)
                for section, fields in (('config', config_fields), ('policy', policy_fields)):
                    declaration = schema['properties'][section]
                    self.assertEqual(declaration['type'], 'object')
                    self.assertEqual(set(declaration['properties']), set(fields.split()))
                    self.assertIs(declaration['additionalProperties'], False)
                validator = Draft202012Validator(schema)
                for valid in ({}, {'config': {}, 'policy': {}}):
                    before = deepcopy(valid)
                    validator.validate(valid)
                    self.assertEqual(valid, before)
                for invalid in ({'future': {}}, {'config': None}, {'policy': None},
                                {'config': {'future': False}}, {'policy': {'future': None}}):
                    with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                        validator.validate(invalid)

    def test_current_provider_flags_remain_boolean_and_policy_is_closed(self):
        expected = {'max_attempts', 'wall_timeout', 'delay_seconds', 'max_delay_seconds',
                    'respect_retry_after', 'jitter'}
        for implementation in (LoopEngine(), RAGComponent(), VisionComponent()):
            with self.subTest(implementation=type(implementation).__name__):
                schema = implementation.describe_config()['properties']['policy']['properties']['provider']
                self.assertEqual(set(schema['properties']), expected)
                self.assertIs(schema['additionalProperties'], False)
                validator = Draft202012Validator(schema)
                for name in ('respect_retry_after', 'jitter'):
                    field = schema['properties'][name]
                    self.assertEqual(set(field), {'type', 'description'})
                    self.assertEqual(field['type'], 'boolean')
                    for value in (True, False):
                        validator.validate({name: value})
                    for value in (None, 0, 1, 'true', {}):
                        with self.subTest(name=name, value=value), self.assertRaises(ValidationError):
                            validator.validate({name: value})
                with self.assertRaises(ValidationError):
                    validator.validate({'future': True})

    def test_current_resource_constraints_do_not_restore_retired_policy(self):
        rag = RAGComponent().describe_config()
        self.assertNotIn('retention', rag['properties']['policy']['properties'])
        for section in ('config', 'policy'):
            with self.subTest(section=section), self.assertRaises(ValidationError):
                Draft202012Validator(rag).validate({section: {'retention': {}}})
        for name in ('query_cache_max_bytes', 'snapshot_cache_max_bytes'):
            capacity = rag['properties']['config']['properties'][name]
            self.assertEqual(set(capacity), {'type', 'description', 'minimum'})
            self.assertEqual(capacity['type'], 'integer')
            self.assertEqual(capacity['minimum'], 0)
            validator = Draft202012Validator(capacity)
            for value in (0, 64):
                validator.validate(value)
            for value in (None, False, -1, '64'):
                with self.subTest(name=name, value=value), self.assertRaises(ValidationError):
                    validator.validate(value)

        tools = MCPComponent().describe_config()['properties']['config']['properties']['tools']
        self.assertIs(tools['additionalProperties'], False)
        selection, = tools['patternProperties'].values()
        self.assertEqual(set(selection['required']), {'server', 'tool'})
        self.assertEqual(set(selection['properties']), {'server', 'tool'})
        self.assertIs(selection['additionalProperties'], False)
        validator = Draft202012Validator(tools)
        validator.validate({})
        validator.validate({'selected': {'server': 'server', 'tool': 'search'}})
        for value in ({'selected': {'server': 'server'}}, {'selected': {'tool': 'search'}},
                      {'selected': {'server': 'server', 'tool': 'search', 'future': True}},
                      {'selected': {'server': '', 'tool': 'search'}}, {'bad/name': {'server': 's', 'tool': 't'}}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validator.validate(value)

        processing = MemoryComponent().describe_config()['properties']['config']['properties']['processing']['properties']
        mode, prompt, destination = (processing[name] for name in ('tool_result_mode', 'tool_summary_prompt', 'tool_data'))
        self.assertEqual(mode, {'type': 'string', 'description': '', 'enum': ['reference', 'summary']})
        self.assertEqual(prompt, {'type': 'string', 'description': '', 'minLength': 1})
        self.assertIs(destination['additionalProperties'], False)
        self.assertEqual(set(destination['required']), {'component', 'identifier'})
        self.assertEqual(destination['properties'], {
            'component': {'type': 'string', 'description': '', 'minLength': 1},
            'identifier': {'type': 'string', 'description': '', 'minLength': 1}})
        for schema, valid, invalid in (
            (mode, 'reference', ('automatic', None)), (prompt, 'summarize', ('', None)),
            (destination, {'component': 'data', 'identifier': 'results'},
             ({'component': 'data'}, {'identifier': 'results'}, {'component': '', 'identifier': 'results'},
              {'component': 'data', 'identifier': 'results', 'future': True})),
        ):
            validator = Draft202012Validator(schema)
            validator.validate(valid)
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValidationError):
                    validator.validate(value)

    def test_current_project_policy_presence_and_limits_are_explicit(self):
        schema = ProjectConfig.describe_policies()
        self.assertEqual(set(schema['properties']), {'approval', 'context', 'output', 'run', 'tool_retry', 'tools', 'usage'})
        self.assertIs(schema['additionalProperties'], False)
        validator = Draft202012Validator(schema)
        values = {'tools': {'allowed_tools': [], 'max_calls': None},
                  'tool_retry': {'max_retries': 0, 'delay_seconds': None},
                  'approval': {'enabled': False, 'rules': []}}
        before = deepcopy(values)
        validator.validate(values)
        self.assertEqual(values, before)
        for value in ({'retention': {}}, {'future': {}}, {'tools': {'max_calls': 0}},
                      {'tools': {'max_calls': False}}, {'tools': {'max_calls': -1}},
                      {'tool_retry': {'max_retries': False}}, {'tool_retry': {'max_retries': -1}}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validator.validate(value)


    def test_persisted_config_domain_fields_and_workflow_unchanged(self):
        config = ProjectConfig.deserialize(json.dumps(self.before['project_config']))
        self.assertEqual(config.to_dict(), self.before['project_config'])
        for domain in (Project, Session, Run, Step):
            expected = list(self.before['fields'][domain.__name__])
            if domain is Project:
                # H2 adds managed storage lifetime without renaming existing fields.
                expected.insert(expected.index('components') + 1, 'initialized_components')
            if domain is Run:
                # B/C adds mandatory Agent identity without renaming existing fields.
                expected.insert(expected.index('paths') + 1, 'agent_id')
            self.assertEqual([item.name for item in fields(domain)], expected)
        graph = WorkflowGraph(entry='end', metadata={'title': 'Review'}).node('end', 'end').to_dict()
        self.assertEqual(graph, self.before['workflow'])

    def test_current_binding_vocabulary_and_complete_payload_are_explicit(self):
        config = ProjectConfig(self.before['project_config'])
        scope = ToolExecutionScope(ToolRuntime(revision='host-1'), policy={'max_calls': 5})
        context = EngineContext(NS(config=config, components=()), NS(config={}), NS(engine='loop'), (), tool_scope=scope)
        self.assertEqual(scope.binding(), self.before['scope_binding'])
        loop = LoopEngine()
        loop._configure({'completion': {'model': 'test/model', 'temperature': None}, 'max_iterations': 3})
        # Capture the complete current payload: do not strip the adapter field
        # to compare against a historical digest.
        original_hash = hashlib.sha256
        with patch('llm.engines.loop.engine.hashlib.sha256', wraps=original_hash) as tracked:
            binding = loop.bind_execution(context)
        payload = json.loads(tracked.call_args.args[0])
        expected = {
            'settings': {'engine': deepcopy(self.before['project_config']['parameters']['engines']['loop']),
                         'parameters': deepcopy(self.before['project_config']['parameters']),
                         'policies': {'tools': {'max_calls': 5}}, 'data': {}},
            'completion': {'model': 'test/model', 'temperature': None},
            'prompt': None, 'agent': {}, 'tools': [], 'tool_contracts': {},
            'adapter': {'type': 'llm.providers.litellm.LiteLLMCompletionAdapter', 'binding': None},
            'tool_policy': deepcopy(self.before['scope_binding']),
        }
        self.assertEqual(payload, expected)
        self.assertEqual(binding, digest(expected))
        graph = GraphEngine(handlers={}, parameter_key='graph').for_agent({"engine": "graph", "engine_options": {"workflow": "flow"}})
        expected = {
            'workflow': deepcopy(self.before['workflow']), 'workflow_id': 'flow', 'revision': '1',
            'handler_bindings': [], 'nested_graphs': [], 'handlers': [],
            'max_steps': None, 'max_parallelism': None, 'timeout_seconds': None,
            'project_config': deepcopy(self.before['project_config']), 'session_config': {},
            'components': [], 'tools': [], 'tool_contracts': {},
            'tool_policy': deepcopy(self.before['scope_binding']),
        }
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
