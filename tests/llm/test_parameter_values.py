"""Independent wire examples for configuration presence, relocation and pointers."""

from copy import deepcopy
import unittest

from jsonschema import Draft202012Validator
from llm.core.implementation_parameters import ImplementationParameters, ParameterLookup
from llm.core.json_data import JsonPointer
from llm.core.parameters import ParameterLayout
from llm.core.schema import checked_schema, narrowing_paths, schema_nodes
from llm.components.workflows.bindings import pointer_parts, read_pointer


class ParameterValuesTests(unittest.TestCase):
    def test_envelope_preserves_omission_null_false_zero_and_detaches(self):
        raw = {"config": {"sdk": {"timeout": None, "enabled": False, "retries": 0, "extra": [1]}}}
        view = ImplementationParameters.read(raw)
        self.assertEqual(view.policy, {})
        self.assertEqual(view.to_dict(), raw)
        for name, value in (("timeout", None), ("enabled", False), ("retries", 0)):
            found = view.select("config.sdk." + name)
            self.assertTrue(found.present)
            self.assertEqual(found.value, value)
        self.assertFalse(view.select("config.sdk.missing").present)
        self.assertFalse(ParameterLookup.read({"x": None}, "x.child").present)
        raw["config"]["sdk"]["extra"].append(2)
        exported = view.config
        exported["sdk"]["extra"].append(3)
        selected = view.select("config.sdk").value
        selected["extra"].append(4)
        self.assertEqual(view.config["sdk"]["extra"], [1])
        self.assertNotIn("policy", view.to_dict())
        replaced = view.with_policy({"max_attempts": 2}).with_config({"timeout": None})
        self.assertEqual(replaced.to_dict(), {"config": {"timeout": None}, "policy": {"max_attempts": 2}})
        self.assertNotIn("policy", view.to_dict())
        for update in (view.with_config, view.with_policy):
            with self.assertRaises(TypeError):
                update(None)
        for invalid in ({"policy": None}, {"config": []}, {"max_attempts": 2}, []):
            with self.subTest(value=invalid), self.assertRaises((ValueError, TypeError)):
                ImplementationParameters.read(invalid)

    def test_layout_preserves_opaque_values_and_rejects_policy_bypass_without_mutation(self):
        layout = ParameterLayout(paths={"sdk": "config.provider", "limit": "policy.max_calls"})
        wire = {"config": {"provider": {"timeout": None, "num_retries": 0}, "extension": {"active": False}},
                "policy": {"max_calls": 2}}
        original = deepcopy(wire)
        explicit = layout.unpack(wire)
        self.assertEqual(explicit, {"sdk": {"timeout": None, "num_retries": 0}, "limit": 2,
                                    "extension": {"active": False}})
        self.assertEqual(layout.pack(explicit), wire)
        explicit["sdk"]["timeout"] = 8
        self.assertEqual(wire, original)
        for forbidden in ("limit", "max_calls"):
            invalid = {"config": {forbidden: 99}, "policy": {"max_calls": 2}}
            before = deepcopy(invalid)
            with self.assertRaisesRegex(ValueError, "declared config/policy path"):
                layout.unpack(invalid)
            self.assertEqual(invalid, before)

    def test_pointer_root_empty_names_and_escaped_separator_are_not_conflated(self):
        document = {"": {"": "nested empty"}, "a/b": {"~name": False}, "rows": [0, None]}
        self.assertIs(read_pointer(document, ""), document)
        self.assertEqual(read_pointer(document, "//"), "nested empty")
        self.assertIs(read_pointer(document, "/a~1b/~0name"), False)
        self.assertIsNone(read_pointer(document, "/rows/1"))
        self.assertEqual(pointer_parts("/~01/~10"), ["~1", "/0"])
        self.assertEqual(JsonPointer.reference(("a/b", "~name")), "#/a~1b/~0name")
        # Configuration observations retain their established leading-separator projection.
        self.assertEqual(JsonPointer.parts("///config/a~1b"), ("config", "a/b"))
        for invalid in (None, "rows/0", "/~", "/~2", "/a~01~x"):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                pointer_parts(invalid)
        for missing in ("/rows/00", "/rows/2", "/absent"):
            with self.subTest(path=missing), self.assertRaises(KeyError):
                read_pointer(document, missing)

    def test_relocated_references_escape_property_names_and_annotations_stay_data(self):
        layout = ParameterLayout(config=("a/b", "alias"))
        source = {"type": "object", "properties": {
            "a/b": {"type": "integer", "minimum": 1},
            "alias": {"$ref": "#/properties/a~1b"}},
            "required": ["a/b"], "additionalProperties": False}
        target = layout.schema(source)
        self.assertEqual(target["properties"]["config"]["properties"]["alias"]["$ref"],
                         "#/properties/config/properties/a~1b")
        validator = Draft202012Validator(target)
        self.assertTrue(validator.is_valid({"config": {"a/b": 2, "alias": 3}}))
        self.assertFalse(validator.is_valid({"config": {"a/b": 2, "alias": 0}}))
        self.assertFalse(validator.is_valid({"config": {"alias": 3}}))
        annotations = {"type": "object", "const": {"default": 1, "$ref": "https://invalid.example"},
                       "x-example": {"type": "number", "x-narrowing": "maximum"}}
        self.assertEqual(checked_schema(annotations), annotations)
        self.assertEqual(len(list(schema_nodes(annotations))), 1)
        self.assertEqual(narrowing_paths(annotations), ())
        with self.assertRaisesRegex(ValueError, "implicit defaults"):
            checked_schema({"type": "object", "properties": {"x": {"default": 1}}})
        with self.assertRaisesRegex(ValueError, "local schema references"):
            checked_schema({"$ref": "https://invalid.example"})
        with self.assertRaisesRegex(ValueError, "inline numeric property path"):
            narrowing_paths({"allOf": [{"type": "number", "x-narrowing": "maximum"}]})
