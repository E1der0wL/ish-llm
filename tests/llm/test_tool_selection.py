"""Independent selection wire, schema, ordering and detached mutation contracts."""

from copy import deepcopy
import unittest
from jsonschema import Draft202012Validator

from llm.components.tools.selection import ToolSelection
from llm.core.implementation_parameters import ImplementationParameters


class ToolSelectionTests(unittest.TestCase):
    def test_absence_and_empty_are_distinct_without_implicit_selection(self):
        for value, configured in (({}, False), ({"enabled": []}, True), ({"enabled": ["two", "one"]}, True)):
            before = deepcopy(value)
            selected = ToolSelection.read(value, scope="tools")
            self.assertEqual(selected.to_dict(), before)
            self.assertIs(selected.configured, configured)
            names = selected.names
            names.append("changed")
            value["enabled"] = ["changed"]
            self.assertEqual(selected.to_dict(), before)
        for value in ({"enabled": None}, {"enabled": "one"}, {"enabled": [1]},
                      {"enabled": ["one", "one"]}, {"extra": []}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ToolSelection.read(value, scope="tools")

    def test_explicit_updates_keep_order_and_configuration_policy(self):
        parameters = ImplementationParameters.read({"config": {"enabled": ["two", "one"]},
                                                    "policy": {"application_owned": False}})
        selected = ToolSelection.read(parameters.config, scope="tools")
        added = selected.add(["one", "three"], scope="tools")
        self.assertEqual(added.names, ["two", "one", "three"])
        removed = added.remove(["two", "absent"], scope="tools")
        self.assertEqual(removed.names, ["one", "three"])
        self.assertEqual(selected.names, ["two", "one"])
        self.assertEqual(parameters.with_config(removed.to_dict()).to_dict(),
                         {"config": {"enabled": ["one", "three"]}, "policy": {"application_owned": False}})
        self.assertEqual(ToolSelection.read({}, scope="tools").add([], scope="tools").to_dict(), {"enabled": []})
        for invalid in ("one", None, [None], ["../escape"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ToolSelection.identifiers(invalid)

    def test_schema_uses_same_field_and_constraints_without_mutable_aliases(self):
        expected = {"type": "object", "properties": {"enabled": {"type": "array",
            "items": {"type": "string"}, "uniqueItems": True}}, "additionalProperties": False}
        self.assertEqual(ToolSelection.schema(), expected)
        choices = ["one", "two"]
        schema = ToolSelection.schema(choices=choices)
        choices.append("changed")
        self.assertEqual(schema["properties"]["enabled"]["items"]["enum"], ["one", "two"])
        schema["properties"]["enabled"]["items"]["type"] = "integer"
        self.assertEqual(ToolSelection.schema(), expected)
        validator = Draft202012Validator(ToolSelection.schema())
        for value in ({}, {"enabled": []}, {"enabled": ["one"]}, {"enabled": None}, {"enabled": [1]}, {"enabled": ["one", "one"]}):
            if validator.is_valid(value):
                self.assertEqual(ToolSelection.read(value, scope="tools").to_dict(), value)
            else:
                with self.assertRaises(ValueError):
                    ToolSelection.read(value, scope="tools")
