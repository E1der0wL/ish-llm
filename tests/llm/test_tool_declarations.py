"""Missing trusted declarations cannot create execution authority."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from llm.components.tools import Tool, ToolContract, ToolRegistry, tool
from llm.services.runtime.tools import ToolCall


DECLARATION = {
    "revision": "explicit-test",
    "effect": "external",
    "isolation": "process",
    "approval_required": True,
    "operation_key_required": True,
    "retry_fields": (),
}


class ToolDeclarationTests(unittest.TestCase):
    def test_public_exports_share_one_contract_and_wire_identity(self):
        from llm.core import tool_contracts as owner
        from llm.components.tools import registry
        from llm.components import tools
        for name in ("ToolClassification", "ToolContract", "ToolEffect", "ToolIsolation"):
            self.assertIs(getattr(registry, name), getattr(owner, name))
            self.assertIs(getattr(tools, name), getattr(owner, name))
        stored = {"revision": "explicit-test", "effect": "external", "isolation": "process",
                  "approval_required": True, "operation_key_required": True, "retry_fields": []}
        self.assertEqual(owner.ToolContract(**DECLARATION).to_dict(), stored)
        self.assertEqual(owner.ToolContract.from_dict(stored).to_dict(), stored)
        self.assertEqual(owner.ToolClassification().to_dict(),
                         {"category": "tool.execute", "risk_scheme": None, "risk": None})

    def test_every_execution_choice_is_required_at_declaration(self):
        for field in DECLARATION:
            candidate = {key: value for key, value in DECLARATION.items() if key != field}
            with self.subTest(field=field), self.assertRaises(TypeError):
                ToolContract(**candidate)

    def test_tool_and_call_require_a_complete_contract(self):
        async def effect(arguments):
            raise AssertionError("An incomplete declaration must never reach the handler")
        with self.assertRaises(TypeError):
            Tool("effect", "effect", {"type": "object"}, effect)
        with self.assertRaises(TypeError):
            Tool("effect", "effect", {"type": "object"}, effect, contract=None)
        with self.assertRaises(TypeError):
            ToolCall("effect", {}, "step")
        for missing in DECLARATION:
            value = ToolContract(**DECLARATION).to_dict()
            value.pop(missing)
            with self.subTest(missing=missing), self.assertRaises((TypeError, ValueError)):
                ToolCall("effect", {}, "step", contract=value)

    def test_registry_revalidates_a_corrupted_trusted_object(self):
        async def effect(arguments):
            raise AssertionError("Registration does not execute")
        value = Tool("effect", "effect", {"type": "object"}, effect,
                     contract=ToolContract(**DECLARATION))
        object.__setattr__(value, "contract", None)
        with self.assertRaises(TypeError):
            ToolRegistry((value,))

    def test_decorator_uses_the_declared_contract_without_another_defaults_path(self):
        async def main():
            """Explicit tool."""
        contract = ToolContract(**DECLARATION)
        self.assertIs(tool(contract, strict=False)(main), main)
        self.assertIs(main.__tool_contract__, contract)
        self.assertIs(main.__tool_options__["strict"], False)
        with self.assertRaises(TypeError):
            tool()
        with self.assertRaises(TypeError):
            tool(None)

    def test_current_reader_is_closed_and_preserves_false_and_empty_values(self):
        declared = {**DECLARATION, "approval_required": False,
                    "operation_key_required": False, "retry_fields": ()}
        value = ToolContract(**declared).to_dict()
        self.assertEqual(ToolContract.from_dict(value).to_dict(), value)
        call = ToolCall("effect", {}, "step", contract=value)
        value["approval_required"] = True
        self.assertIs(call.contract["approval_required"], False)
        for field in DECLARATION:
            candidate = ToolContract(**declared).to_dict()
            candidate.pop(field)
            before = deepcopy(candidate)
            with self.subTest(field=field), self.assertRaises((TypeError, ValueError)):
                ToolContract.from_dict(candidate)
            self.assertEqual(candidate, before)
        for invalid in (None, {}, [], False, {**value, "legacy": True},
                        {**value, "retry_fields": "path"}, {**value, "approval_required": 0}):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                ToolContract.from_dict(invalid)

    def test_operation_reader_rejects_missing_contract_fields_without_reclaiming(self):
        from llm.services.runtime.operations import OperationRepository, operation_token
        with tempfile.TemporaryDirectory() as root:
            session = SimpleNamespace(id="session", project_id="project",
                paths=SimpleNamespace(state=Path(root) / "state", root=Path(root)))
            repository = OperationRepository()
            repository.initialize(session)
            call = ToolCall("effect", {}, "step", "project", "session", "run", "business-key",
                operation_token("project", "session", "business-key"),
                contract=ToolContract(**DECLARATION).to_dict())
            repository.claim(session, call)
            path = repository._path(session, "business-key")
            record = json.loads(path.read_text())
            for field in DECLARATION:
                candidate = deepcopy(record)
                candidate["contract"].pop(field)
                path.write_text(json.dumps(candidate))
                before = path.read_bytes()
                with self.subTest(field=field), self.assertRaises((TypeError, ValueError)):
                    repository.claim(session, call)
                self.assertEqual(path.read_bytes(), before)
