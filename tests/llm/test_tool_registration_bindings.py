"""Registration wire remains detached and cannot invent handler authority."""

from copy import deepcopy
from dataclasses import dataclass, replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from llm.components.tools import Tool, ToolClassification, ToolContract, ToolRegistry
from llm.components.tools.bindings import ToolHandlerBindings
from llm.core.model_protocol import ToolDefinition


CONTRACT = {"revision": "explicit", "effect": "read_only", "isolation": "none",
            "approval_required": False, "operation_key_required": False, "retry_fields": []}


class ToolRegistrationBindingTests(unittest.TestCase):
    def test_registry_copy_reuses_private_definitions_and_detaches_all_public_data(self):
        class Handler:
            def __deepcopy__(self, memo):
                raise AssertionError('runtime handler copied')
        handler = Handler()
        schema = {'type': 'object', 'properties': {'n': {'type': 'integer'}}, 'required': ['n']}
        registry = ToolRegistry((Tool('read', 'read', schema, handler,
            definition=ToolDefinition('read', 'read', schema), contract=ToolContract.from_dict(CONTRACT)),))
        with patch('llm.components.tools.registry.copy_model', side_effect=AssertionError('definition copied')):
            selected = registry.copy()
            self.assertIs(selected.get_handler('read'), handler)
        for item in (selected.get('read'), registry.get('read')):
            item.parameters['properties']['n']['type'] = 'string'
            item.definition.parameters['properties'].clear()
        selected.definitions()[0].parameters.clear()
        for target in (registry, selected):
            self.assertEqual(target.get('read').parameters, schema)
            with self.assertRaises(ValueError):
                target.prepare('read', {'n': 'bad'})
        # Preserve prepare's live-Tool contract without sharing exposed state.
        prepared, _ = selected.prepare('read', {'n': 1})
        prepared.parameters['properties']['n']['type'] = 'string'
        self.assertEqual(registry.get('read').parameters, schema)
        later = selected.copy()
        prepared.parameters['properties']['n']['type'] = 'boolean'
        self.assertEqual(later.get('read').parameters['properties']['n']['type'], 'string')
        selected.register(self.tool(handler))
        self.assertEqual(registry.names(), ('read',))

    def test_registry_copy_retains_subclass_slots_and_validation(self):
        class Custom(ToolRegistry):
            __slots__ = ('limits',)
            def prepare(self, *args, **kwargs):
                raise ValueError('custom admission')
        original = Custom()
        original.limits = {'values': [1]}
        copied = original.copy()
        copied.limits['values'].append(2)
        self.assertEqual(original.limits, {'values': [1]})
        with self.assertRaisesRegex(ValueError, 'custom admission'):
            copied.prepare('read', {})

    def test_concurrent_copy_and_prepare_keep_exposed_definition_isolated(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with ThreadPoolExecutor(max_workers=2) as pool:
            for _ in range(20):
                registry = ToolRegistry((self.tool(SimpleNamespace()),))
                barrier = Barrier(2)
                def copy_registry():
                    barrier.wait(timeout=5)
                    return registry.copy()
                def expose_tool():
                    barrier.wait(timeout=5)
                    return registry.prepare('selected', {})[0]
                copied, exposed = pool.submit(copy_registry), pool.submit(expose_tool)
                copied, exposed = copied.result(timeout=5), exposed.result(timeout=5)
                exposed.parameters['description'] = 'original only'
                self.assertNotIn('description', copied.get('selected').parameters)
                cloned, _ = copied.prepare('selected', {})
                cloned.parameters['description'] = 'copy only'
                self.assertEqual(registry.get('selected').parameters['description'], 'original only')

    def tool(self, handler, *, classification=None, definition=None):
        return Tool("selected", "description", {"type": "object"}, handler,
                    definition=definition, classification=classification,
                    contract=ToolContract.from_dict(CONTRACT))

    def test_selection_and_extension_reuse_only_exact_schema_validation(self):
        tool = self.tool(SimpleNamespace())
        with patch.object(Draft202012Validator, 'check_schema',
                          wraps=Draft202012Validator.check_schema) as check:
            registry = ToolRegistry([tool])
            selected = registry.select(('selected',))
            combined = ToolRegistry()
            combined.extend(selected)
            self.assertEqual(check.call_count, 1)
            self.assertEqual(combined.execution_binding({}), registry.execution_binding({}))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                combined.extend(selected)
        # Actual arguments are still validated each time, even after reuse.
        original = Tool('typed', 'typed', {'type': 'object', 'properties': {'n': {'type': 'integer'}},
                        'required': ['n'], 'additionalProperties': False}, SimpleNamespace(),
                        contract=ToolContract.from_dict(CONTRACT))
        copied = ToolRegistry([original]).select(('typed',))
        for arguments in ({}, {'n': '1'}, {'n': 1, 'extra': True}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                copied.prepare('typed', arguments)
        self.assertEqual(copied.prepare('typed', {'n': 1})[1], {'n': 1})

    def test_schema_mutation_cannot_reuse_proof_and_copies_remain_detached(self):
        tool = self.tool(SimpleNamespace())
        registry = ToolRegistry([tool])
        selected = registry.select(('selected',))
        tool.parameters['type'] = 'array'
        registry.get('selected').parameters['type'] = 'array'
        self.assertEqual(selected.get('selected').parameters, {'type': 'object'})
        # prepare has always exposed the registered Tool. Even mutation through
        # that surface must not let a later selection borrow an old proof.
        registered, _ = registry.prepare('selected', {})
        registered.parameters['properties'] = {'value': {'type': 'not-a-type'}}
        with self.assertRaises(SchemaError):
            registry.select(('selected',))
        with self.assertRaises(SchemaError):
            ToolRegistry().extend(registry)
        self.assertEqual(selected.get('selected').parameters, {'type': 'object'})

    def test_extension_preserves_registration_override_and_live_binding_checks(self):
        calls = []
        class Custom(ToolRegistry):
            def register(self, tool):
                calls.append(tool.name)
                super().register(tool)

        handler = SimpleNamespace(execution_binding={'revision': 'first'})
        source = ToolRegistry([self.tool(handler)])
        destination = Custom()
        destination.extend(source)
        self.assertEqual(calls, ['selected'])
        handler.execution_binding['revision'] = 'second'
        self.assertEqual(destination.contracts()['selected']['execution_binding'], {'revision': 'second'})
        # Definition matching is not bypassed by cached parameter syntax.
        registered, _ = source.prepare('selected', {})
        object.__setattr__(registered, 'definition', ToolDefinition('other', 'description', {'type': 'object'}))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            source.select(('selected',))

    def test_non_json_schema_extensions_do_not_borrow_json_proofs(self):
        registry = ToolRegistry([self.tool(SimpleNamespace())])
        tool = Tool('second', 'description', {'type': 'object', 'x-extension': {1: 'value'}},
                    SimpleNamespace(), contract=ToolContract.from_dict(CONTRACT))
        with patch.object(Draft202012Validator, 'check_schema',
                          wraps=Draft202012Validator.check_schema) as check:
            registry.register(tool)
            registry.select(('second',))
            self.assertEqual(check.call_count, 2)

    def test_derived_bindings_reuse_syntax_but_validate_new_contracts_and_definitions(self):
        source = ToolRegistry([self.tool(SimpleNamespace())])
        tool = source.get('selected')
        handler = SimpleNamespace(execution_binding={'revision': 'new'})
        changed = replace(tool, handler=handler, contract=replace(tool.contract, revision='new', approval_required=True))
        with patch.object(Draft202012Validator, 'check_schema', wraps=Draft202012Validator.check_schema) as check:
            derived = source.derive((changed,))
            self.assertEqual(check.call_count, 0)
            self.assertTrue(derived.get('selected').contract.approval_required)
            self.assertEqual(derived.contracts()['selected']['execution_binding'], {'revision': 'new'})
            self.assertFalse(source.get('selected').contract.approval_required)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                source.derive((replace(changed, definition=ToolDefinition('wrong', 'description', tool.parameters)),))
            with self.assertRaisesRegex(ValueError, 'retry_fields'):
                source.derive((replace(changed, contract=replace(tool.contract, retry_fields=('missing',))),))
            with self.assertRaises(SchemaError):
                source.derive((replace(changed, parameters={'type': 'object', 'properties': {'n': {'type': 'invalid'}}}),))

    def test_definitions_preserve_constraints_extensions_and_detachment(self):
        schema = {'type': 'object', 'properties': {'n': {'type': 'integer'}}, 'required': ['n']}
        definition = ToolDefinition('selected', 'description', schema, strict=True, opaque={'extension': [1]})
        source = ToolRegistry([replace(self.tool(SimpleNamespace()), parameters=schema, definition=definition)])
        result = source.definitions(constraints={'selected': {'n': {'mode': 'fixed', 'value': 3}}})[0]
        self.assertTrue(result.strict)
        self.assertEqual(result.opaque, {'extension': [1]})
        Draft202012Validator(result.parameters).validate({'n': 3})
        self.assertFalse(Draft202012Validator(result.parameters).is_valid({'n': 4}))
        result.parameters.clear()
        result.opaque['extension'].append(2)
        fresh = source.definitions()[0]
        self.assertEqual(fresh.parameters, schema)
        self.assertEqual(fresh.opaque, {'extension': [1]})

    def test_exact_binding_wire_optional_presence_and_detached_extensions(self):
        extension = {1: ("opaque", {"version": "original"})}
        handler = SimpleNamespace(execution_binding=extension)
        registry = ToolRegistry([self.tool(handler, classification=ToolClassification())])
        policy = {"allowed_tools": ["selected"]}
        value = registry.execution_binding(policy)
        self.assertEqual(value, {
            "tools": [{"name": "selected", "description": "description", "parameters": {"type": "object"},
                       "strict": None, "opaque": {}}],
            "tool_contracts": {"selected": {"classification": {"category": "tool.execute", "risk_scheme": None,
                                                               "risk": None}, **CONTRACT,
                                            "execution_binding": extension}},
            "tool_policy": policy})
        value["tool_contracts"]["selected"]["execution_binding"][1][1]["version"] = "changed"
        value["tool_policy"]["allowed_tools"].clear()
        self.assertEqual(extension, {1: ("opaque", {"version": "original"})})
        self.assertEqual(policy, {"allowed_tools": ["selected"]})
        plain = ToolRegistry([self.tool(SimpleNamespace())]).execution_binding(None)
        self.assertEqual(plain["tool_contracts"], {"selected": CONTRACT})
        self.assertIsNone(plain["tool_policy"])
        for bad in (float("nan"), object()):
            handler.execution_binding = bad
            with self.subTest(value=bad), self.assertRaises((TypeError, ValueError)):
                registry.contracts()

    def test_public_registry_overrides_and_opaque_policy_shape_are_preserved(self):
        class CustomRegistry(ToolRegistry):
            def definitions(self):
                return (ToolDefinition("custom", "custom description", {"type": "object"}, strict=False),)

            def contracts(self):
                return {"custom": {"application": {"revision": "original"}}}

        @dataclass
        class OpaquePolicy:
            value: list

        policy = OpaquePolicy(["original"])
        value = CustomRegistry().execution_binding(policy)
        self.assertEqual(value["tools"][0]["name"], "custom")
        self.assertIs(value["tools"][0]["strict"], False)
        self.assertEqual(value["tool_contracts"], {"custom": {"application": {"revision": "original"}}})
        self.assertIsInstance(value["tool_policy"], OpaquePolicy)
        value["tool_policy"].value.append("changed")
        self.assertEqual(policy.value, ["original"])

    def test_static_selection_discovery_does_not_invoke_properties_or_dynamic_hooks(self):
        accesses = []
        class Handler:
            @property
            def bind_arguments(self):
                accesses.append("read")
                return lambda arguments, context: "binding"

        handler = Handler()
        self.assertTrue(ToolHandlerBindings.declares_selection(handler))
        self.assertEqual(accesses, [])
        tool = self.tool(handler)
        object.__setattr__(tool, "contract", ToolContract.from_dict({**CONTRACT, "retry_fields": ["value"]}))
        object.__setattr__(tool, "parameters", {"type": "object", "properties": {"value": {"type": "string"}}})
        with self.assertRaisesRegex(ValueError, "Durable selected Tool bindings"):
            ToolRegistry([tool])
        self.assertEqual(accesses, [])
        self.assertEqual(ToolHandlerBindings.declared_binder(handler)({}, None), "binding")
        self.assertEqual(accesses, ["read"])
        class Dynamic:
            def __getattr__(self, name):
                accesses.append(name)
                return lambda *args: "not declared"
        self.assertIsNone(ToolHandlerBindings.declared_binder(Dynamic()))
        self.assertEqual(accesses, ["read"])

    def test_registration_and_batch_preparation_reject_before_handler_use(self):
        class Handler:
            async def __call__(self, arguments):
                raise AssertionError("Registration/preparation cannot execute")
        registry = ToolRegistry([self.tool(Handler())])
        for value in ([], None, {"value": float("inf")}, {"value": object()}, {"value": (1, 2)}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Unknown tool or invalid tool arguments"):
                registry.prepare("selected", value)
        arguments = {"value": ["original"]}
        _, prepared = registry.prepare("selected", arguments)
        prepared["value"].clear()
        self.assertEqual(arguments, {"value": ["original"]})
        for schema in ({}, {"type": "array"}, {"type": ["object", "null"]}):
            tool = self.tool(Handler())
            object.__setattr__(tool, "parameters", deepcopy(schema))
            with self.subTest(schema=schema), self.assertRaisesRegex(ValueError, "Tool parameters must describe an object"):
                ToolRegistry([tool])
            with self.assertRaisesRegex(ValueError, "Tool parameters must describe an object"):
                ToolDefinition("selected", "description", schema)
