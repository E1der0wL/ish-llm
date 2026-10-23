"""Registration wire remains detached and cannot invent handler authority."""

from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
import unittest

from llm.components.tools import Tool, ToolClassification, ToolContract, ToolRegistry
from llm.components.tools.bindings import ToolHandlerBindings
from llm.core.model_protocol import ToolDefinition


CONTRACT = {"revision": "explicit", "effect": "read_only", "isolation": "none",
            "approval_required": False, "operation_key_required": False, "retry_fields": []}


class ToolRegistrationBindingTests(unittest.TestCase):
    def tool(self, handler, *, classification=None, definition=None):
        return Tool("selected", "description", {"type": "object"}, handler,
                    definition=definition, classification=classification,
                    contract=ToolContract.from_dict(CONTRACT))

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
