"""Hub creation defaults share the settings codec and provider boundary."""
from importlib.util import find_spec
import unittest

import jsonschema

from dok import DockConfig, SettingsError
from dok.integrations.hub import HubAgents
from dok.integrations.hub_config import HubConfig
from dok.settings.jsonc import Jsonc


class HubConfigTests(unittest.TestCase):
    def test_creation_defaults_roundtrip_without_requiring_a_live_hub(self):
        for values in ({}, {"model": "provider/test", "api_key": "test-key", "api_base": "https://example.invalid/v1"},
                       {"api_key": "", "model": None}):
            with self.subTest(values=values):
                config = DockConfig(hub=values)
                jsonschema.validate(config.to_dict(), DockConfig.schema())
                self.assertEqual(DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(config))), config)
        self.assertEqual(DockConfig().hub, HubConfig())
        self.assertEqual(DockConfig.initial().hub.to_dict(), {"model": None, "api_key": None, "api_base": None})
        for values in ({"model": " "}, {"api_key": 123}, {"api_base": []}, {"unknown": "value"}):
            with self.subTest(values=values):
                with self.assertRaises(SettingsError):
                    DockConfig(hub=values)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate({"hub": values}, DockConfig.schema())

    @unittest.skipUnless(find_spec("hub") and find_spec("llm"), "optional Hub + llm contracts")
    def test_templates_use_provider_fields_and_do_not_materialize_null_overrides(self):
        from llm.core.agents import AgentDefinition
        from llm.providers.request_values import LiteLLMParameters as P

        field = AgentDefinition.Field.COMPLETION
        efforts = {"command-options": "low", "command-analysis": "medium", "calendar": "medium", "shell-assistant": "medium"}
        defaults = HubAgents._definition()
        self.assertEqual(set(defaults.agents), set(efforts))
        for identifier, value in defaults.agents.items():
            self.assertEqual(value[field], {P.REASONING_EFFORT: efforts[identifier]})
            self.assertNotIn(P.TEMPERATURE, value[field])
        selected = HubConfig(model="provider/test", api_key="test-key", api_base="https://example.invalid/v1")
        expected = {P.MODEL: selected.model, P.API_KEY: selected.api_key, P.API_BASE: selected.api_base}
        definition = HubAgents._definition(selected)
        for identifier, value in definition.agents.items():
            self.assertEqual(value[field], {**expected, P.REASONING_EFFORT: efforts[identifier]})
        partial = HubAgents._definition(HubConfig(api_key=""))
        for identifier, value in partial.agents.items():
            self.assertEqual(value[field], {P.API_KEY: "", P.REASONING_EFFORT: efforts[identifier]})
        with self.assertRaisesRegex(ValueError, "api_base"):
            HubAgents._definition(HubConfig(api_base="not-a-url"))
