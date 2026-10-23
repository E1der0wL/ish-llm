"""Target selection and editing preserve explicit configuration evidence."""

from copy import deepcopy
import unittest

from llm.core.models import ProjectConfig
from llm.core.configuration_targets import ParameterTargets, SessionConfiguration, ResolvedEngineConfiguration


class ConfigurationTargetTests(unittest.TestCase):
    def test_target_presence_and_detached_envelopes_preserve_original_wire(self):
        raw = {"engines": {"empty": {}, "writer": {"config": {"temperature": 0, "timeout": None},
                                                "policy": {"enabled": False}}}}
        before = deepcopy(raw)
        targets = ParameterTargets.read(raw)
        self.assertTrue(targets.has_engine("empty"))
        self.assertFalse(targets.has_engine("missing"))
        self.assertEqual(targets.engine("missing").to_dict(), {})
        self.assertEqual(targets.component_names, ())
        self.assertEqual(targets.to_dict(), raw)
        projected = targets.engine("writer").config
        projected["temperature"] = 99
        raw["engines"]["writer"]["config"]["temperature"] = 4
        self.assertEqual(targets.to_dict(), before)
        changed = targets.with_engine("other", {"config": {"custom": [False, None]}})
        self.assertEqual(changed.engine_names, ("empty", "writer", "other"))
        self.assertEqual(targets.engine_names, ("empty", "writer"))
        self.assertNotIn("components", changed.to_dict())
        self.assertEqual(ParameterTargets.read({}).without_engine("missing").to_dict(), {})

    def test_invalid_target_update_does_not_change_project(self):
        config = ProjectConfig(parameters={"components": {"memory": {"config": {"custom": [1]}}}},
                               policies={"usage": {"max_calls": 2}}, data={"application": True})
        before = config.to_dict()
        for invalid in ({"policy": None}, {"timeout": 2}, {"config": {"bad": float("nan")}}):
            with self.subTest(value=invalid), self.assertRaises((ValueError, TypeError)):
                config.set_component_parameters("memory", invalid)
            self.assertEqual(config.to_dict(), before)
        config.set_component_parameters("memory", {"config": {"custom": [2]}})
        self.assertEqual(config.parameter_targets.component("memory").config, {"custom": [2]})
        self.assertEqual(config.policies, before["policies"])
        self.assertEqual(config.data, before["data"])
        config.remove_component_parameters("memory")
        self.assertEqual(config.parameters, {"components": {}})

    def test_session_scope_rejects_component_and_policy_authority(self):
        self.assertEqual(SessionConfiguration.read({}).to_dict(), {})
        from llm.core.implementation_parameters import ImplementationParameters
        selected = SessionConfiguration.read({"data": {"note": "keep"}}).with_engine(
            "writer", ImplementationParameters.read({}).with_config({"timeout": None}))
        self.assertEqual(selected.to_dict(), {"data": {"note": "keep"},
            "parameters": {"engines": {"writer": {"config": {"timeout": None}}}}})
        for forbidden in ({"parameters": {"components": {}}}, {"policies": {}},
                          {"parameters": {"engines": {"writer": {"policy": None}}}}):
            with self.subTest(value=forbidden), self.assertRaises((ValueError, TypeError)):
                SessionConfiguration.read(forbidden)
        session = {"parameters": {"engines": {"writer": {"config": {"timeout": None}}}}, "data": {"note": "s"}}
        project = ProjectConfig(parameters={"engines": {"writer": {
            "config": {"timeout": 10, "custom": False}, "policy": {"provider": {"max_attempts": 2}}}}},
            data={"note": "p", "retained": 0})
        original = project.to_dict()
        resolved = project.resolve_engine_config("writer", session)
        self.assertEqual(resolved["engine"], {"config": {"timeout": None, "custom": False},
                                            "policy": {"provider": {"max_attempts": 2}}})
        self.assertEqual(resolved["data"], {"note": "s", "retained": 0})
        view = ResolvedEngineConfiguration.read(resolved)
        self.assertEqual(view.engine.to_dict(), resolved["engine"])
        resolved["engine"]["config"]["timeout"] = 9
        self.assertIsNone(view.engine.config["timeout"])
        self.assertEqual(project.to_dict(), original)
        with self.assertRaises(KeyError):
            ResolvedEngineConfiguration.read(original)

    def test_export_redaction_preserves_draft_omission_without_admitting_it(self):
        raw = {"parameters": {"engines": {"_hub_title": {"config": {"model": "private"}},
                                         "writer": {"config": {"timeout": None}}}}, "data": {"draft": True}}
        before = deepcopy(raw)
        result = ProjectConfig.without_engine_parameters(raw, "_hub_title")
        self.assertEqual(result, {"parameters": {"engines": {"writer": {"config": {"timeout": None}}}},
                                  "data": {"draft": True}})
        self.assertEqual(raw, before)
        self.assertNotIn("policies", result)
        self.assertEqual(ProjectConfig.without_engine_parameters({}, "_hub_title"), {})
        draft = {"data": {"note": "edit"}, "unrecognized": {"keep": [1]}}
        self.assertEqual(ProjectConfig.without_engine_parameters(draft, "_hub_title"), draft)
        with self.assertRaises(ValueError):
            ProjectConfig.from_dict(draft)
        for invalid in ({"engines": {1: {}}}, {"engines": {"writer": {"config": {"x": object()}}}}):
            with self.subTest(value=invalid), self.assertRaises(TypeError):
                ParameterTargets.read(invalid)

    def test_form_schema_projection_detaches_without_inventing_fields(self):
        from llm.services.schema import ProjectFormSchemas, ProjectConfiguration
        source = {"properties": {"title": {"type": "string"}, "components": {"type": "array"},
            "config": {"properties": {"data": {"type": "object"}, "policies": {"type": "object"},
                "parameters": {"properties": {"engines": {"properties": {"writer": {"description": "engine"}}},
                                              "components": {"properties": {"memory": {"description": "component"}}}}}}}},
            "x-engines": {"writer": {"configuration_key": "writer"}}}
        before = deepcopy(source)
        forms = ProjectFormSchemas.read(source)
        self.assertEqual(forms.engines, {"writer": {"description": "engine"}})
        self.assertEqual(forms.components, {"memory": {"description": "component"}})
        self.assertEqual(set(forms.general["properties"]), {"title", "config"})
        self.assertEqual(set(forms.general["properties"]["config"]["properties"]), {"data", "policies"})
        forms.engines["writer"]["description"] = "edited"
        forms.general["x-engines"]["writer"]["configuration_key"] = "edited"
        self.assertEqual(source, before)
        self.assertEqual(ProjectConfiguration.engine_parameter_schemas(source), before["properties"]["config"]
                         ["properties"]["parameters"]["properties"]["engines"]["properties"])
        self.assertEqual(ProjectConfiguration.engine_form_path("writer"), ("config", "parameters", "engines", "writer"))
        self.assertEqual(ProjectConfiguration.component_form_path("memory"), ("config", "parameters", "components", "memory"))
