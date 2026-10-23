"""Wire omission, inherited observations and open host bindings remain distinct."""

from copy import deepcopy
import unittest

from llm.core.results import EngineOutput
from llm.engines.agent_values import (AgentExecutionBinding, AgentNodeDefinition,
                                     AgentOutputValue, AgentStepMetadata)


class AgentValueTests(unittest.TestCase):
    def test_output_schema_distinguishes_absent_data_from_json_null_and_falsey_values(self):
        text = EngineOutput(text="null")
        self.assertEqual(AgentOutputValue.from_output(text, "text").to_dict(), {"text": "null"})
        self.assertEqual(AgentOutputValue.from_output(text, "json").to_dict(), {"text": "null", "data": None})
        self.assertEqual(AgentOutputValue.workflow(None).to_dict(), {"text": "", "data": None})
        for data in (False, 0, "", [], {}):
            with self.subTest(data=data):
                output = EngineOutput(text="not JSON", data=data)
                value = AgentOutputValue.from_output(output, "json")
                self.assertEqual(value.to_dict(), {"text": "not JSON", "data": data})
                self.assertIs(value.apply_to(output), output)
        with self.assertRaises(ValueError):
            AgentOutputValue.from_output(EngineOutput(text="not JSON"), "json")

    def test_open_binding_and_explicit_null_observations_are_not_normalized_or_repaired(self):
        raw = {"agent_id": "selected", "agent_revision": "revision", "engine": "worker",
               "host": {"selected": [None, False]}}
        original = deepcopy(raw)
        binding = AgentExecutionBinding(raw)
        raw["host"]["selected"].clear()
        projected = binding.to_dict()
        self.assertEqual(projected, original)
        projected["host"]["selected"].clear()
        self.assertEqual(binding.state(), {"agent": original})
        metadata = {"agent_id": None, "agent_revision": None, "engine": None,
                    "node_id": None, "agent_step_id": None, "extension": {"x": False}}
        self.assertEqual(AgentStepMetadata.behavior_child(metadata, binding, "node", "step"), metadata)
        self.assertEqual(AgentStepMetadata.graph_child(metadata, "step"), metadata)
        self.assertEqual(AgentStepMetadata.started(binding, None), {**original, "inputs": None})
        with self.assertRaises(KeyError):
            AgentExecutionBinding({}).agent_id

    def test_missing_node_reference_keeps_original_required_key_diagnostic(self):
        with self.assertRaises(KeyError) as raised:
            AgentNodeDefinition({}).references()
        self.assertEqual(raised.exception.args, ("agent",))
