"""Selected Agent forms share the owning Engine's validation and no values."""

from copy import copy, deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.exceptions import SchemaError

from llm.core.agents import AgentDefinition
from llm.core.models import RunStatus
from llm.core.schema import checked_schema, nest_schema, object_schema, schema_nodes, reuse_schema_checks
from llm.engines.base import BaseEngine
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine
from llm.engines.registry import EngineRegistry
from llm.llm import LargeLanguageModel
from llm.providers.litellm import LiteLLMCompletionAdapter


class SchemaReuseTests(unittest.TestCase):
    def test_exact_proofs_are_nested_detached_and_operation_local(self):
        value = object_schema({'n': {'type': 'integer'}})
        with patch.object(Draft202012Validator, 'check_schema', wraps=Draft202012Validator.check_schema) as check:
            with reuse_schema_checks():
                first = checked_schema(value)
                with reuse_schema_checks():
                    second = checked_schema(value)
                self.assertEqual(check.call_count, 1)
                first['properties']['n']['type'] = 'string'
                self.assertEqual(second, value)
                changed = deepcopy(value)
                changed['properties']['n']['type'] = 'invalid'
                for _ in range(2):
                    with self.assertRaises(SchemaError):
                        checked_schema(changed)
                self.assertEqual(check.call_count, 3)
            checked_schema(value)
            self.assertEqual(check.call_count, 4)

    def test_failure_resets_scope_and_non_json_extensions_cannot_share_proofs(self):
        value = object_schema()
        with patch.object(Draft202012Validator, 'check_schema', wraps=Draft202012Validator.check_schema) as check:
            with self.assertRaisesRegex(RuntimeError, 'abort'):
                with reuse_schema_checks():
                    checked_schema(value)
                    raise RuntimeError('abort')
            with reuse_schema_checks():
                checked_schema(value)
                extension = {**value, 'x-extension': [1]}
                checked_schema(extension)
                checked_schema({**value, 'x-extension': (1,)})
                checked_schema({**value, 'x-extension': (1,)})
            self.assertEqual(check.call_count, 5)

    def test_binding_keeps_live_schema_hooks_instance_checks_and_semantic_checks(self):
        class Owner(BaseEngine):
            def __init__(self):
                super().__init__()
                self.schema = AgentDefinition.describe()
                self.described = self.bound = 0

            def describe_agent(self):
                self.described += 1
                return checked_schema(self.schema)

            def for_agent(self, definition):
                self.bound += 1
                Draft202012Validator(self.describe_agent()).validate(definition)
                return self

        owner = Owner()
        registry = EngineRegistry()
        registry.register('owner', owner)
        with patch.object(Draft202012Validator, 'check_schema', wraps=Draft202012Validator.check_schema) as check:
            registry.resolve_agent({'engine': 'owner'})
            self.assertEqual(check.call_count, 1)
            self.assertEqual((owner.described, owner.bound), (2, 1))
            owner.schema['properties']['engine'] = {'const': 'different'}
            with self.assertRaises(ValueError):
                registry.resolve_agent({'engine': 'owner'})
            self.assertEqual(check.call_count, 2)
            self.assertEqual(owner.bound, 1)


class SelectedAgentSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.loop = LoopEngine()
        self.graph = GraphEngine(handlers={})
        self.app = LargeLanguageModel(self.root, engines={'writer': self.loop, 'flow': self.graph})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate()

    async def schema(self, engine):
        return (await self.project.agents.adescribe(engine=engine))['schema']

    async def test_observation_does_not_bind_execute_or_materialize_values(self):
        before = self.project.paths.record.read_bytes()
        config = deepcopy(self.project.data.config.to_dict())
        for target, name in ((self.loop, 'for_agent'), (self.graph, 'for_agent'),
                             (self.loop.completion_adapter, 'validate_request'),
                             (self.loop.completion_adapter, 'stream')):
            setattr(target, name, Mock(side_effect=AssertionError('schema query must not execute')))
        general = await self.project.agents.adescribe()
        self.assertEqual(general['schema'], AgentDefinition.schema)
        self.assertEqual(set(general['engines']), {'writer', 'flow'})
        for name in general['engines']:
            schema = await self.schema(name)
            Draft202012Validator.check_schema(schema)
            self.assertEqual(schema['allOf'][-1]['properties']['engine']['const'], name)
            self.assertFalse(any('default' in node for node in schema_nodes(schema)))
        self.assertEqual(await self.project.agents.alist(), {})
        self.assertEqual(self.project.data.config.to_dict(), config)
        self.assertEqual(self.project.paths.record.read_bytes(), before)

    async def test_loop_form_preserves_agent_model_and_engine_policy_separation(self):
        schema = await self.schema('writer')
        properties = schema['properties']
        completion = properties['completion']
        self.assertEqual(completion, self.loop.completion_adapter.describe_config())
        self.assertTrue(completion['additionalProperties'])
        options = properties['engine_options']
        self.assertEqual(options, self.loop.describe_config())
        self.assertNotIn('completion', options['properties']['config']['properties'])
        self.assertIn('completion', options['properties']['policy']['properties'])
        draft = {'engine': 'writer', 'completion': {'temperature': None, 'extra_body': {'private': 7}},
                 'engine_options': {'policy': {'max_iterations': 2}}}
        Draft202012Validator(schema).validate(draft)
        await self.project.agents.acreate(draft, identifier='draft')
        self.assertEqual(await self.project.agents.aload('draft'), draft)
        snapshot = await self.project.agents.asnapshot('draft')
        changed = {**draft, 'completion': {'temperature': 0}}
        await self.project.agents.arevise('draft', changed, expected_revision=snapshot['revision'])
        self.assertEqual(await self.project.agents.aload('draft'), changed)
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await self.project.agents.arevise('draft', draft, expected_revision=snapshot['revision'])

    async def test_loop_form_and_storage_reject_same_invalid_options(self):
        schema = await self.schema('writer')
        for extra in ({'engine_options': {'policy': {'max_iterations': 0}}},
                      {'engine_options': {'config': {'completion': {}}}},
                      {'engine_options': {'policy': {'typo': 1}}},
                      {'completion': {'temperature': 'hot'}}, {'system_promt': 'typo'}):
            with self.subTest(extra=extra):
                definition = {'engine': 'writer', **extra}
                self.assertFalse(Draft202012Validator(schema).is_valid(definition))
                with self.assertRaises((ValueError, ValidationError)):
                    await self.project.agents.acreate(definition, identifier='invalid')
        self.assertEqual(await self.project.agents.alist(), {})

    async def test_graph_form_contains_only_workflow_agent_contract(self):
        schema = await self.schema('flow')
        self.assertEqual(set(schema['properties']), set(AgentDefinition.WORKFLOW_FIELDS))
        options = schema['properties']['engine_options']
        self.assertIn('workflow', options['required'])
        definition = {'engine': 'flow', 'engine_options': {'workflow': 'review'},
                      'metadata': {'ui': {'label': 'Review'}}}
        Draft202012Validator(schema).validate(definition)
        await self.project.agents.acreate(definition, identifier='review')
        self.assertEqual(await self.project.agents.aload('review'), definition)
        for extra in ({'completion': {}}, {'tools': []}, {'resources': {}}, {'system_prompt': None},
                      {'policy': {}}, {'output_schema': {}}, {'output_format': 'json'},
                      {'input_schema': {}}, {'foo': 'unknown'},
                      {'engine_options': {}}, {'engine_options': {'workflow': ' '}},
                      {'engine_options': {'workflow': 'review', 'policy': {'foo': 1}}}):
            with self.subTest(extra=extra):
                invalid = {**definition, **extra}
                self.assertFalse(Draft202012Validator(schema).is_valid(invalid))
                with self.assertRaises((ValueError, ValidationError)):
                    await self.project.agents.acreate(invalid, identifier='invalid')

    async def test_description_is_detached_and_selects_only_requested_engine(self):
        self.graph.describe_agent = Mock(side_effect=AssertionError('unselected Engine'))
        first = await self.schema('writer')
        first['properties']['completion']['properties'].clear()
        second = await self.schema('writer')
        self.assertIn('model', second['properties']['completion']['properties'])
        self.graph.describe_agent.assert_not_called()
        self.assertFalse(Draft202012Validator(second).is_valid({'engine': 'flow'}))
        with self.assertRaisesRegex(ValueError, 'Unregistered Agent engine'):
            await self.schema('missing')

    async def test_custom_engine_owns_unknown_private_option_without_parent_changes(self):
        class Planner(BaseEngine):
            def __init__(self):
                super().__init__()
                self.bindings = []

            def describe_agent(self):
                return AgentDefinition.describe(
                    properties={'engine_options': object_schema({
                        'config': object_schema({'strategy': {'enum': ['local', 'remote']}}, required=['strategy']),
                    }, required=['config'])}, required=('engine_options',))

            def for_agent(self, definition):
                self.bindings.append(deepcopy(definition))
                result = copy(self)
                result.strategy = definition['engine_options']['config']['strategy']
                return result

            async def run(self, context):
                yield self.strategy

        planner = Planner()
        self.app.engines.register('planner', planner)
        schema = await self.schema('planner')
        self.assertEqual(planner.bindings, [])
        definition = {'engine': 'planner', 'engine_options': {'config': {'strategy': 'remote'}}}
        Draft202012Validator(schema).validate(definition)
        await self.project.agents.acreate(definition, identifier='planner')
        invalid = {'engine': 'planner', 'engine_options': {'config': {'strategy': 'invalid'}}}
        count = len(planner.bindings)
        with self.assertRaisesRegex(ValueError, "invalid"):
            await self.project.agents.acreate(invalid, identifier='invalid')
        self.assertEqual(len(planner.bindings), count)
        session = await self.project.sessions.acreate()
        await session.asave(agent_ids=['planner'])
        run, = await (await session.run.submit('plan', recipients=['planner'])).wait()
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)

    async def test_no_schema_hook_does_not_invent_form_or_remove_existing_execution_contract(self):
        class Action(BaseEngine):
            async def run(self, context):
                yield 'done'

        self.app.engines.register('action', Action())
        await self.project.agents.acreate({'engine': 'action'}, identifier='action')
        with self.assertRaisesRegex(ValueError, 'does not provide describe_agent'):
            await self.schema('action')
        self.assertEqual(await self.project.agents.aload('action'), {'engine': 'action'})


class AgentSchemaCompositionTests(unittest.TestCase):
    def test_selected_adapter_schema_references_keep_their_local_owner(self):
        class Adapter(LiteLLMCompletionAdapter):
            def describe_config(self):
                return object_schema({'mode': {'$ref': '#/$defs/mode'}},
                    **{'$defs': {'mode': {'enum': ['fast', 'exact']}}})

            def validate_request(self, request, **kwargs):
                Draft202012Validator(self.describe_config()).validate(request)

        registry = EngineRegistry()
        adapter = Adapter()
        original = adapter.describe_config()
        registry.register('custom', LoopEngine(completion_adapter=adapter))
        schema = registry.describe_agent('custom')
        self.assertEqual(schema['properties']['completion']['properties']['mode']['$ref'],
                         '#/$defs/mode')
        self.assertIn('$id', schema['properties']['completion'])
        for mode, accepted in [('fast', True), ('invalid', False)]:
            definition = {'engine': 'custom', 'completion': {'mode': mode}}
            self.assertEqual(Draft202012Validator(schema).is_valid(definition), accepted)
            if accepted:
                registry.resolve_agent(definition)
            else:
                with self.assertRaises(ValueError):
                    registry.resolve_agent(definition)
        self.assertEqual(adapter.describe_config(), original)

    def test_invalid_owner_schemas_fail_without_fabricating_values(self):
        registry = EngineRegistry()
        class Owner(BaseEngine):
            def describe_agent(self):
                return self.schema
        owner = Owner()
        registry.register('owner', owner)
        for change in ({'additionalProperties': True}, {'type': 'array'}, {'required': []},
                       {'properties': {'engine': {'type': 'string'}, 'unexpected': {}}},
                       {'default': {}}):
            owner.schema = {**AgentDefinition.describe(), **change}
            with self.subTest(change=change), self.assertRaises((ValueError, ValidationError)):
                registry.describe_agent('owner')

    def test_async_schema_hook_is_rejected(self):
        class Owner(BaseEngine):
            async def describe_agent(self):
                return AgentDefinition.describe()
        registry = EngineRegistry()
        registry.register('owner', Owner())
        with self.assertRaisesRegex(TypeError, 'synchronous'):
            registry.describe_agent('owner')

    def test_selected_schema_cannot_widen_common_field_types(self):
        class Owner(BaseEngine):
            def describe_agent(self):
                schema = AgentDefinition.describe()
                schema['properties']['system_prompt'] = {}
                return schema

        registry = EngineRegistry()
        registry.register('owner', Owner())
        schema = registry.describe_agent('owner')
        self.assertFalse(Draft202012Validator(schema).is_valid({'engine': 'owner', 'system_prompt': 3}))

    def test_boolean_property_schema_keeps_selected_engine_constraint(self):
        class Owner(BaseEngine):
            def describe_agent(self):
                return AgentDefinition.describe(properties={'engine': True})

        registry = EngineRegistry()
        registry.register('owner', Owner())
        schema = registry.describe_agent('owner')
        self.assertTrue(Draft202012Validator(schema).is_valid({'engine': 'owner'}))
        self.assertFalse(Draft202012Validator(schema).is_valid({'engine': 'other'}))
        self.assertFalse(Draft202012Validator(schema).is_valid({'engine': 5}))

    def test_registered_name_does_not_overwrite_owners_engine_constraint(self):
        class Owner(BaseEngine):
            def describe_agent(self):
                return AgentDefinition.describe(properties={'engine': {'const': 'different'}})

        registry = EngineRegistry()
        registry.register('owner', Owner())
        schema = registry.describe_agent('owner')
        definition = {'engine': 'owner'}
        self.assertFalse(Draft202012Validator(schema).is_valid(definition))
        with self.assertRaises(ValueError):
            registry.resolve_agent(definition)

    def test_identified_adapter_keeps_owner_validation_and_binding(self):
        class Adapter(LiteLLMCompletionAdapter):
            def describe_config(self):
                return {'$id': 'urn:test:completion', **super().describe_config()}

        engine = LoopEngine(completion_adapter=Adapter())
        registry = EngineRegistry()
        registry.register('custom', engine)
        schema = registry.describe_agent('custom')
        self.assertEqual(schema['properties']['completion'], engine.completion_adapter.describe_config())
        valid = {'engine': 'custom', 'completion': {'model': 'test/model'}}
        engine.for_agent(valid)
        Draft202012Validator(schema).validate(valid)
        registry.resolve_agent(valid)
        invalid = {'engine': 'custom', 'completion': {'model': 42}}
        self.assertFalse(Draft202012Validator(schema).is_valid(invalid))
        with self.assertRaises(ValueError):
            registry.resolve_agent(invalid)

    def test_aliased_adapter_references_are_not_rewritten_twice(self):
        class Adapter(LiteLLMCompletionAdapter):
            def __init__(self):
                shared = {'$ref': '#/$defs/mode'}
                self.schema = object_schema({'left': shared, 'right': shared},
                    **{'$defs': {'mode': {'enum': ['fast', 'accurate']}}})

            def describe_config(self):
                return self.schema

            def validate_request(self, request, **kwargs):
                Draft202012Validator(self.schema).validate(request)

        adapter = Adapter()
        original = deepcopy(adapter.schema)
        registry = EngineRegistry()
        registry.register('custom', LoopEngine(completion_adapter=adapter))
        schema = registry.describe_agent('custom')
        # Validate the exported JSON too: Python alias identity is not a
        # requirement placed on the UI or a persisted definition consumer.
        validator = Draft202012Validator(json.loads(json.dumps(schema)))
        for completion, accepted in (({'left': 'fast', 'right': 'accurate'}, True),
                                      ({'left': 'invalid', 'right': 'accurate'}, False),
                                      ({'left': 'fast', 'right': 'invalid'}, False)):
            definition = {'engine': 'custom', 'completion': completion}
            with self.subTest(completion=completion):
                self.assertEqual(validator.is_valid(definition), accepted)
                if accepted:
                    registry.resolve_agent(definition)
                else:
                    with self.assertRaises(ValueError):
                        registry.resolve_agent(definition)
        self.assertEqual(adapter.schema, original)
        self.assertIs(adapter.schema['properties']['left'], adapter.schema['properties']['right'])

    def test_engine_selection_preserves_original_pointer_locations(self):
        class Owner(BaseEngine):
            def describe_agent(self):
                schema = AgentDefinition.describe()
                schema['properties']['engine'] = {
                    '$defs': {'kind': {'enum': ['owner', 'alias']}},
                    '$ref': '#/properties/engine/$defs/kind',
                    'allOf': [{'minLength': 2}],
                }
                return schema

        owner = Owner()
        registry = EngineRegistry()
        registry.register('owner', owner)
        original = owner.describe_agent()
        schema = registry.describe_agent('owner')
        selected = schema['properties']['engine']
        for key, value in original['properties']['engine'].items():
            self.assertEqual(selected[key], value)
        Draft202012Validator(schema).validate({'engine': 'owner'})
        registry.resolve_agent({'engine': 'owner'})
        for name in ('alias', 'invalid'):
            self.assertFalse(Draft202012Validator(schema).is_valid({'engine': name}))
        self.assertEqual(owner.describe_agent(), original)

    def test_boolean_false_and_existing_const_remain_at_original_location(self):
        class Owner(BaseEngine):
            def describe_agent(self):
                schema = AgentDefinition.describe()
                schema['properties']['engine'] = self.selected
                return schema

        owner = Owner()
        registry = EngineRegistry()
        registry.register('owner', owner)
        for selected, accepted in ((False, False), ({'const': 'owner'}, True),
                                   ({'const': 'other'}, False)):
            owner.selected = selected
            with self.subTest(selected=selected):
                schema = registry.describe_agent('owner')
                self.assertEqual(schema['properties']['engine'], selected)
                self.assertEqual(Draft202012Validator(schema).is_valid({'engine': 'owner'}), accepted)
                self.assertFalse(Draft202012Validator(schema).is_valid({'engine': 'other'}))

    def test_selection_does_not_narrow_an_aliased_property(self):
        class Owner(BaseEngine):
            def describe_agent(self):
                shared = {'type': 'string', '$id': 'urn:test:shared-string'}
                schema = AgentDefinition.describe()
                schema['properties']['engine'] = shared
                schema['properties']['description'] = shared
                return schema

        registry = EngineRegistry()
        registry.register('owner', Owner())
        schema = registry.describe_agent('owner')
        Draft202012Validator(schema).validate({'engine': 'owner', 'description': 'some description'})
        self.assertNotIn('const', schema['properties']['description'])
        self.assertFalse(Draft202012Validator(schema).is_valid({'engine': 'other'}))

    def test_common_envelope_does_not_mutate_an_aliased_composition_list(self):
        class Owner(BaseEngine):
            def describe_agent(self):
                schema = AgentDefinition.describe()
                shared = [True]
                schema['allOf'] = shared
                schema['properties']['engine_options'] = {'type': 'object', 'allOf': shared}
                return schema

            def for_agent(self, definition):
                Draft202012Validator(self.describe_agent()).validate(definition)
                return copy(self)

        registry = EngineRegistry()
        registry.register('owner', Owner())
        definition = {'engine': 'owner', 'engine_options': {}}
        schema = registry.describe_agent('owner')
        Draft202012Validator(schema).validate(definition)
        registry.resolve_agent(definition)
        self.assertEqual(schema['properties']['engine_options']['allOf'], [True])
        invalid = {**definition, 'engine_options': []}
        self.assertFalse(Draft202012Validator(schema).is_valid(invalid))
        with self.assertRaises(ValueError):
            registry.resolve_agent(invalid)

    def test_schema_helper_cannot_add_agent_top_level_fields(self):
        for options in ({'fields': ('metadata',)}, {'fields': ('engine', 'unknown')},
                        {'properties': {'new_authority': {}}}, {'required': ('unknown',)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                AgentDefinition.describe(**options)


class NestedSchemaResourceTests(unittest.TestCase):
    def assert_preserved(self, schema, valid, invalid):
        original = deepcopy(schema)
        nested = nest_schema(schema, ('properties', 'value'))
        self.assertEqual(nested, nest_schema(schema, ('properties', 'value')))
        composed = object_schema({'value': nested}, required=['value'])
        validator = Draft202012Validator(json.loads(json.dumps(composed)))
        Draft202012Validator.check_schema(composed)
        for values, accepted in ((valid, True), (invalid, False)):
            for value in values:
                with self.subTest(value=value, accepted=accepted):
                    self.assertEqual(Draft202012Validator(schema).is_valid(value), accepted)
                    self.assertEqual(validator.is_valid({'value': value}), accepted)
        self.assertEqual(schema, original)
        return nested

    def test_explicit_resource_ids_and_nested_scopes(self):
        for identifier in ('urn:test:resource', 'https://example.invalid/schema', 'owner.json'):
            schema = object_schema({
                'outer': {'$ref': '#/$defs/kind'},
                'inner': {'$id': 'child.json', '$defs': {'kind': {'type': 'integer'}},
                          '$ref': '#/$defs/kind'},
            }, **{'$id': identifier, '$defs': {'kind': {'type': 'string'}}})
            with self.subTest(identifier=identifier):
                nested = self.assert_preserved(schema, [{'outer': 'text', 'inner': 3}],
                                              [{'outer': 3}, {'inner': 'text'}])
                self.assertEqual(nested, schema)

    def test_anonymous_anchors_are_isolated_between_owners(self):
        def owner(kind):
            return {'$ref': '#choice', '$defs': {'value': {'$anchor': 'choice', 'type': kind}}}

        schema = object_schema({name: nest_schema(owner(kind), ('properties', name))
                                for name, kind in (('left', 'string'), ('right', 'integer'))})
        validator = Draft202012Validator(json.loads(json.dumps(schema)))
        validator.validate({'left': 'text', 'right': 3})
        self.assertFalse(validator.is_valid({'left': 3, 'right': 'text'}))

    def test_empty_resource_identity_keeps_local_references(self):
        for identifier in ('', '#'):
            with self.subTest(identifier=identifier):
                self.assert_preserved({'$id': identifier, '$ref': '#/$defs/value',
                                       '$defs': {'value': {'type': 'integer'}}}, [3], ['text'])

    def test_recursive_root_pointer_and_dynamic_anchor(self):
        for reference, extra in (('$ref', {}), ('$dynamicRef', {'$dynamicAnchor': 'node'})):
            schema = object_schema({'value': {'type': 'integer'}, 'child': {
                'anyOf': [{'type': 'null'}, {reference: '#node' if extra else '#'}],
            }}, required=['value'], **extra)
            with self.subTest(reference=reference):
                self.assert_preserved(schema,
                    [{'value': 1}, {'value': 1, 'child': {'value': 2, 'child': None}}],
                    [{'value': 'bad'}, {'value': 1, 'child': {'value': 'bad'}}])

    def test_shared_reference_in_different_resource_scopes(self):
        shared = {'$ref': '#/$defs/kind'}
        schema = object_schema({
            'outer': shared,
            'inner': object_schema({'value': shared}, **{
                '$id': 'urn:test:inner', '$defs': {'kind': {'type': 'integer'}}}),
        }, **{'$defs': {'kind': {'type': 'string'}}})
        self.assert_preserved(schema, [{'outer': 'text', 'inner': {'value': 3}}],
                              [{'outer': 3}, {'inner': {'value': 'text'}}])

    def test_escaped_pointers_and_annotation_data_are_preserved(self):
        schema = {'$ref': '#/$defs/a~1b~0c', '$defs': {'a/b~c': {'const': {
            '$ref': 'https://example.invalid/not-a-schema', '$id': 'data', 'default': 7,
        }}}}
        self.assert_preserved(schema, [schema['$defs']['a/b~c']['const']], [{}])

    def test_reference_free_schemas_are_unchanged_and_detached(self):
        for schema in (True, False, object_schema({'value': {'type': 'string'}})):
            with self.subTest(schema=schema):
                nested = nest_schema(schema, ('properties', 'value'))
                self.assertEqual(nested, schema)
                if isinstance(schema, dict):
                    nested['properties'].clear()
                    self.assertIn('value', schema['properties'])

    def test_external_refs_and_implicit_defaults_remain_rejected(self):
        for schema in ({'$ref': 'https://example.invalid/schema'},
                       {'$id': 'urn:test:owner', '$dynamicRef': 'external.json#node'},
                       {'$id': 'urn:test:owner', 'default': {}},
                       {'$defs': {'value': {'$ref': 'external.json'}}}):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                nest_schema(schema, ('properties', 'value'))


class SchemaIdentityTests(unittest.TestCase):
    @staticmethod
    def owner(kind, identifier='inner.json'):
        return object_schema({'value': {
            '$id': identifier, '$ref': '#/$defs/type', '$defs': {'type': {'type': kind}},
        }}, required=['value'])

    def test_relative_resources_remain_independent_in_forms_and_binding(self):
        left, right = self.owner('string'), self.owner('integer')
        originals = deepcopy((left, right))

        class Owner(BaseEngine):
            calls = 0

            def describe_agent(self):
                return AgentDefinition.describe(properties={'completion': left, 'engine_options': right})

            def for_agent(self, definition):
                self.calls += 1
                Draft202012Validator(left).validate(definition['completion'])
                Draft202012Validator(right).validate(definition['engine_options'])
                return copy(self)

        engine = Owner()
        registry = EngineRegistry()
        registry.register('custom', engine)
        schema = registry.describe_agent('custom')
        self.assertEqual(engine.calls, 0)
        validator = Draft202012Validator(json.loads(json.dumps(schema)))
        for text, number, valid in (('text', 3, True), ('text', 'wrong', False), (3, 3, False)):
            definition = {'engine': 'custom', 'completion': {'value': text}, 'engine_options': {'value': number}}
            with self.subTest(definition=definition):
                self.assertEqual(validator.is_valid(definition), valid)
                count = engine.calls
                if valid:
                    registry.resolve_agent(definition)
                    self.assertEqual(engine.calls, count + 1)
                else:
                    with self.assertRaises(ValueError):
                        registry.resolve_agent(definition)
                    self.assertEqual(engine.calls, count)
        self.assertEqual((left, right), originals)

    def test_conflicting_explicit_ids_fail_before_form_or_factory(self):
        class Owner(BaseEngine):
            for_agent = Mock(side_effect=AssertionError('conflicting schema must not bind'))

            def describe_agent(self):
                return AgentDefinition.describe(properties={
                    'completion': SchemaIdentityTests.owner('string', 'urn:test:shared'),
                    'engine_options': SchemaIdentityTests.owner('integer', 'urn:test:shared'),
                })

        owner = Owner()
        registry = EngineRegistry()
        registry.register('custom', owner)
        for action in (lambda: registry.describe_agent('custom'),
                       lambda: registry.resolve_agent({'engine': 'custom'})):
            with self.subTest(action=action), self.assertRaises(ValueError) as raised:
                action()
            message = str(raised.exception)
            self.assertIn('urn:test:shared', message)
            self.assertIn('#/properties/completion/properties/value', message)
            self.assertIn('#/properties/engine_options/properties/value', message)
        owner.for_agent.assert_not_called()

    def test_identical_explicit_resources_can_be_shared_or_copied(self):
        shared = {'$id': 'urn:test:shared', '$ref': '#/$defs/value', '$defs': {'value': {'type': 'integer'}}}
        for other in (shared, deepcopy(shared)):
            with self.subTest(aliased=other is shared):
                schema = checked_schema(object_schema({'left': shared, 'right': other}))
                validator = Draft202012Validator(json.loads(json.dumps(schema)))
                validator.validate({'left': 3, 'right': 4})
                self.assertFalse(validator.is_valid({'left': 3, 'right': 'wrong'}))

    def test_relative_ids_are_compared_after_resolution(self):
        for same in (True, False):
            schema = object_schema({
                'left': {'$id': 'defs/../shared.json', 'type': 'integer'},
                'right': {'$id': 'shared.json#', 'type': 'integer' if same else 'string'},
            }, **{'$id': 'https://example.invalid/root/schema.json'})
            with self.subTest(same=same):
                if same:
                    self.assertEqual(checked_schema(schema), schema)
                else:
                    with self.assertRaisesRegex(ValueError, 'https://example.invalid/root/shared.json'):
                        checked_schema(schema)

    def test_relative_ids_of_shared_nodes_use_each_owners_base(self):
        shared = self.owner('integer')['properties']['value']
        schema = object_schema({key: object_schema({'value': shared}, **{'$id': base})
            for key, base in (('left', 'https://example.invalid/a/'), ('right', 'https://example.invalid/b/'))})
        checked = checked_schema(schema)
        Draft202012Validator(checked).validate({'left': {'value': 3}, 'right': {'value': 4}})
        self.assertEqual(shared['$id'], 'inner.json')

    def test_anchors_are_unique_within_a_resource_but_reusable_across_resources(self):
        for keyword in ('$anchor', '$dynamicAnchor'):
            with self.subTest(keyword=keyword):
                one = {keyword: 'value', 'type': 'integer'}
                two = {keyword: 'value', 'type': 'string'}
                with self.assertRaisesRegex(ValueError, 'value'):
                    checked_schema({'$defs': {'one': one, 'two': two}})
                checked_schema({'$defs': {'one': one, 'copy': deepcopy(one)}})
                checked_schema({'$defs': {'one': {'$id': 'urn:test:one', **one},
                                         'two': {'$id': 'urn:test:two', **two}}})
        checked_schema({'$anchor': 'value', '$dynamicAnchor': 'value', 'type': 'integer'})

    def test_inactive_schema_branches_are_checked_but_instance_data_is_not(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting schema resource'):
            checked_schema({'anyOf': [True, {'$id': 'urn:test:duplicate', 'type': 'integer'},
                                     {'$id': 'urn:test:duplicate', 'type': 'string'}]})
        schema = object_schema({'literal': {'enum': [
            {'$id': 'urn:test:duplicate', '$anchor': 'same', 'type': 'integer'},
            {'$id': 'urn:test:duplicate', '$anchor': 'same', 'type': 'string'},
        ]}})
        self.assertEqual(checked_schema(schema), schema)

    def test_empty_child_id_cannot_replace_the_parent_resource(self):
        for identifier in ('', '#'):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, 'Conflicting schema resource'):
                checked_schema(object_schema({'child': {'$id': identifier, 'type': 'string'}}))
