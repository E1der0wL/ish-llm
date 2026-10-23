"""Configuration query projections preserve explicit settings and source evidence."""

from copy import deepcopy
import unittest

from llm.core.configuration_views import ConfigurationView
from llm.core.configuration import resolve_config, resolve_engine_config


class ConfigurationValueTests(unittest.TestCase):
    def test_sources_overrides_and_pointer_escaping_have_independent_wire_values(self):
        project = {"a/b": {"~key": 1}, "keep": False}
        session = {"a/b": {"~key": None}}
        host = {"keep": True}
        before = deepcopy((project, session, host))
        result = resolve_config([("project", project), ("session", session)], host=host)
        self.assertEqual(result, {
            "values": {"a/b": {"~key": None}, "keep": True},
            "sources": {"/a~1b/~0key": "session", "/keep": "host"},
            "overridden": {"/a~1b/~0key": [{"source": "project", "value": 1}],
                           "/keep": [{"source": "project", "value": False}]},
            "editable": {"/a~1b/~0key": True, "/keep": False},
        })
        self.assertEqual((project, session, host), before)
        view = ConfigurationView.read(result)
        self.assertIsNone(view.values["a/b"]["~key"])
        self.assertEqual(view.overridden["/keep"][0].source, "project")
        self.assertIs(view.overridden["/keep"][0].value, False)

    def test_nested_views_and_extensions_are_detached_without_losing_omission(self):
        raw = {"values": {"config": {"opaque": [1.0]}}, "future": {"policy": [None]}}
        expected = deepcopy(raw)
        view = ConfigurationView.read(raw)
        raw["values"]["config"]["opaque"].clear()
        view.values["config"]["opaque"].append(2)
        view.to_dict()["future"]["policy"].clear()
        self.assertEqual(view.to_dict(), expected)
        self.assertEqual(view.sources, {})
        self.assertEqual(view.editable, {})
        self.assertEqual(view.runtime, ())
        self.assertNotIn("sources", view.to_dict())
        self.assertEqual(ConfigurationView.read({}).to_dict(), {})
        self.assertEqual(ConfigurationView.unavailable().to_dict(), {"runtime_only": True})

    def test_present_invalid_observations_are_not_repaired(self):
        for raw in ({"values": None}, {"sources": {"/x": 1}}, {"editable": {"/x": 1}},
                    {"overridden": {"/x": [{"value": False}]}}, {"runtime": None},
                    {"runtime": [1]}, {"overridden": {"/x": None}}):
            before = deepcopy(raw)
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                ConfigurationView.read(raw)
            self.assertEqual(raw, before)

    def test_engine_key_and_runtime_are_query_only_and_keep_explicit_null(self):
        config = {"parameters": {"engines": {"chosen": {"config": {"model": None}}}}}
        before = deepcopy(config)
        result = resolve_engine_config(config, "chosen")
        self.assertEqual(result["configuration_key"], "chosen")
        self.assertEqual(result["values"], {"config": {"model": None}})
        self.assertNotIn("runtime", result)
        view = ConfigurationView.read(result).with_runtime(("completion",))
        self.assertEqual(view.runtime, ("completion",))
        self.assertEqual(view.to_dict()["runtime"], ["completion"])
        self.assertEqual(view.to_dict()["values"], {"config": {"model": None}})
        self.assertEqual(config, before)
        self.assertNotIn("runtime", result)

    def test_scalar_to_object_override_preserves_original_source_history(self):
        result = resolve_config([("earlier", {"nested": False}), ("later", {"nested": {"child": None}})])
        self.assertEqual(result["overridden"], {"/nested": [{"source": "earlier", "value": False}]})
        self.assertEqual(result["sources"], {"/nested/child": "later"})
        self.assertEqual(result["values"], {"nested": {"child": None}})
