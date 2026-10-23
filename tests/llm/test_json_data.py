"""JSON syntax validation preserves open data and never repairs domain evidence."""

from copy import deepcopy
from enum import StrEnum
import math
import unittest

from llm.core.json_data import JsonObject
from llm.core.models import ProjectConfig


class JsonDataTests(unittest.TestCase):
    def test_open_json_values_and_numeric_types_are_unchanged(self):
        class Text(StrEnum):
            VALUE = "value"
        raw = {"unknown": [None, True, False, 0, 1.0, "", {"future": Text.VALUE}]}
        expected = deepcopy(raw)
        self.assertIsNone(JsonObject.validate(raw))
        self.assertEqual(raw, expected)
        self.assertIs(type(raw["unknown"][3]), int)
        self.assertIs(type(raw["unknown"][4]), float)
        self.assertIs(raw["unknown"][6]["future"], Text.VALUE)

    def test_invalid_container_keys_values_and_nonfinite_numbers_are_rejected(self):
        cases = [([], TypeError, "JSON data must be a dictionary"),
                 ({1: "text"}, TypeError, "JSON keys must be strings"),
                 ({"nested": [{False: "text"}]}, TypeError, "JSON keys must be strings"),
                 ({"nested": (1, 2)}, TypeError, "JSON data must contain only JSON values"),
                 ({"nested": b"text"}, TypeError, "JSON data must contain only JSON values")]
        for raw, kind, message in cases:
            with self.subTest(raw=raw), self.assertRaisesRegex(kind, message):
                JsonObject.validate(raw)
        for value in (math.nan, math.inf, -math.inf):
            raw = {"nested": [value]}
            with self.subTest(value=value), self.assertRaises(ValueError):
                JsonObject.validate(raw)
            self.assertIs(raw["nested"][0], value)

    def test_public_project_entry_point_uses_the_same_validator_contract(self):
        raw = {"custom": {"value": [None, False, 1.0]}}
        before = deepcopy(raw)
        self.assertIsNone(ProjectConfig.validate_json(raw))
        self.assertEqual(raw, before)
        for invalid in (None, {"value": object()}, {"value": float("inf")}):
            errors = []
            for validate in (JsonObject.validate, ProjectConfig.validate_json):
                try:
                    validate(invalid)
                except (ValueError, TypeError) as error:
                    errors.append((type(error), str(error)))
                else:
                    self.fail("Invalid JSON was accepted")
            self.assertEqual(errors[0], errors[1])

    def test_json_validation_does_not_supply_missing_domain_fields(self):
        raw = {"metadata": {}}
        self.assertIsNone(JsonObject.validate(raw))
        self.assertEqual(raw, {"metadata": {}})
        with self.assertRaises(ValueError):
            from llm.core.continuations import ResumeRecord
            ResumeRecord.read(raw)
        self.assertEqual(raw, {"metadata": {}})
