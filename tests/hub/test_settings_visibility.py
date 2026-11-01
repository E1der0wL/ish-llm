"""Essential UI projection must preserve the complete backend configuration."""

from copy import deepcopy
import unittest

from jsonschema import Draft202012Validator

from hub.locales import Language
from hub.widget.forms.settings import SchemaForm
from hub.widget.forms.visibility import EssentialSettings as Visible
from llm.components.memory import MemoryComponent
from llm.components.rag import RAGComponent
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.registry import EngineRegistry


class EssentialSettingsTests(unittest.TestCase):
    def form(self, schema, values, scope, name='', **kwargs):
        return SchemaForm(schema, values, Language('en'), presentation=Visible(scope, name), **kwargs)

    def test_same_engine_policy_in_project_and_agent_preserves_advanced_values(self):
        registry = EngineRegistry()
        registry.register('loop', LoopEngine())
        schema = registry.describe_agent('loop')
        display = deepcopy(schema)
        display.pop('allOf', None)
        values = {'engine': 'loop', 'completion': {'model': 'local/model', 'seed': 71,
                  'extra_body': {'future': None}}, 'metadata': {'title': 'Coder'},
                  'engine_options': {'config': {'buffer_size': 19},
                      'policy': {'provider': {'max_attempts': 3, 'delay_seconds': 1.5},
                                 'completion': {'max_tokens': 204800, 'reserve_tokens': 4096}}}}
        agent = self.form(display, values, Visible.AGENT, 'loop')
        engine = self.form(LoopEngine().describe_config(), values['engine_options'], Visible.ENGINE, 'loop')
        self.assertEqual({p[1:] for p in agent.visible_fields if p[0] == 'engine_options'}, set(engine.visible_fields))
        self.assertIn(('completion', 'model'), agent.visible_fields)
        self.assertNotIn(('completion', 'seed'), agent.visible_fields)
        self.assertNotIn(('metadata',), agent.visible_fields)
        self.assertNotIn(('config', 'buffer_size'), engine.visible_fields)
        self.assertIn(('policy', 'completion', 'max_tokens'), engine.visible_fields)
        self.assertIn(('policy', 'provider', 'max_attempts'), engine.visible_fields)
        agent.fields[('completion', 'model')].text = 'local/other'
        actual = agent.values()
        expected = deepcopy(values)
        expected['completion']['model'] = 'local/other'
        self.assertEqual(actual, expected)
        Draft202012Validator(schema).validate(actual)

    def test_nullable_policies_keep_absence_null_unknowns_and_locked_siblings(self):
        schema = LoopEngine().describe_config()
        for value in ({}, {'policy': {'completion': None}},
                      {'policy': {'completion': {'max_tokens': 204800, 'counter': 'custom'}}}):
            with self.subTest(value=value):
                form = self.form(schema, value, Visible.ENGINE, 'loop')
                self.assertEqual(form.values(), value)
                form.fields[('policy', 'max_iterations')].text = '25'
                expected = deepcopy(value)
                expected.setdefault('policy', {})['max_iterations'] = 25
                self.assertEqual(form.values(), expected)
        schema = {'type': 'object', 'properties': {'completion': {'type': ['object', 'null'],
            'properties': {'model': {'type': 'string'},
                           'seed': {'type': 'integer'}}}}}
        # Public host-lock metadata is tested with the actual owner spelling.
        from llm.core.schema import SchemaExtension
        schema['properties']['completion']['properties']['model'][SchemaExtension.HOST_OVERRIDE] = True
        form = self.form(schema, {'completion': {'model': 'fixed', 'seed': 7}}, Visible.AGENT, 'loop')
        form.fields[('completion','model')].text = 'other'
        self.assertEqual(form.values()['completion'], {'model': 'fixed', 'seed': 7})

    def test_required_connections_custom_extensions_and_graph_workflow_remain_available(self):
        schema = {'type':'object', 'required':['future_required'], 'properties': {
            'future_required': {'type':'string'}, 'optional_extra': {'type':'integer'}}}
        self.assertIn(('future_required',), self.form(schema, {}, Visible.PROJECT).visible_fields)
        self.assertEqual(len(self.form(schema, {}, Visible.COMPONENT, 'custom').visible_fields), 2)
        registry = EngineRegistry()
        registry.register('graph', GraphEngine(handlers={}))
        graph = deepcopy(registry.describe_agent('graph'))
        graph.pop('allOf', None)
        form = self.form(graph, {'engine':'graph'}, Visible.AGENT, 'graph')
        self.assertIn(('engine_options', 'workflow'), form.visible_fields)
        self.assertFalse(any(p[0] == 'completion' for p in form.visible_fields))

    def test_component_models_are_visible_but_internal_processing_tuning_is_json_only(self):
        for name, component, model_path, advanced in (
            ('memory', MemoryComponent(), ('config','processing','completion','model'),
             ('policy','processing','model_input_chars')),
            ('rag', RAGComponent(), ('config','embedding_params','model'), ('config','chunk_size')),
        ):
            with self.subTest(component=name):
                schema = component.describe_config()
                original = {'config': {'custom_extension': {'unknown': None}}}
                form = self.form(schema, original, Visible.COMPONENT, name)
                self.assertIn(model_path, form.visible_fields)
                self.assertNotIn(advanced, form.visible_fields)
                self.assertEqual(form.values(), original)
                self.assertEqual(form.schema, schema)
                self.assertIsNone(form.fields[advanced]._input)

    def test_json_edit_still_validates_hidden_constraints_and_does_not_select_a_union_branch(self):
        schema = LoopEngine().describe_config()
        form = self.form(schema, {'config': {'buffer_size': 0}}, Visible.ENGINE, 'loop')
        self.assertNotIn(('config','buffer_size'), form.visible_fields)
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(form.values())))
        union = {'type':'object', 'properties': {'future': {'oneOf': [
            {'type':'object', 'properties':{'a':{'type':'string'}}}, {'type':'array'}]}}}
        form = self.form(union, {'future':['kept']}, Visible.COMPONENT, 'custom')
        self.assertEqual(set(form.visible_fields), {('future',)})
        self.assertEqual(form.values(), {'future':['kept']})

    def test_empty_components_and_nonessential_policies_stay_json_only(self):
        for name in ('skills', 'mcp', 'prompts', 'workflows'):
            form = self.form({'type': 'object'}, {}, Visible.COMPONENT, name)
            self.assertFalse(form.visible_fields)
            self.assertEqual(form.values(), {})
        for name, schema, values in (
            ('goals', {'type': 'object', 'properties': {'policy': {'type':'object',
                       'properties': {'inject': {'type':'boolean'}}}}}, {'policy':{'inject':False}}),
            ('refinement', {'type':'object'}, {}),
        ):
            form = self.form(schema, values, Visible.COMPONENT, name)
            self.assertFalse(form.visible_fields)
            self.assertEqual(form.values(), values)

    def test_resource_metadata_uses_creation_time_without_mutating_the_input(self):
        from datetime import datetime
        from hub.backend.project.management_values import ResourceTimestamps, DefinitionSummary
        original = {'metadata': {'title': 'Agent'}}
        created = ResourceTimestamps.created(original)
        stamp = created['metadata']['created_at']
        self.assertIsNotNone(datetime.fromisoformat(stamp).tzinfo)
        self.assertEqual(created['metadata']['updated_at'], stamp)
        self.assertEqual(original, {'metadata': {'title': 'Agent'}})
        changed = ResourceTimestamps.modified(created)
        self.assertEqual(changed['metadata']['created_at'], stamp)
        self.assertGreaterEqual(changed['metadata']['updated_at'], stamp)
        self.assertEqual(DefinitionSummary.entry('a', {'metadata': {'created_at':stamp}}, 'v')['modified'], stamp)
        self.assertEqual(DefinitionSummary.entry('a', {}, 'v')['modified'], '')
