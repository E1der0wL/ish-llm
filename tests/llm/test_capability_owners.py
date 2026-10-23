"""Capability identifiers stay open and Agent source projections do not repair records."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from llm.components.processing import COMPLETION_PROCESSORS
from llm.components.tools.registry import ToolRegistry
from llm.components.tools.resolver import ComponentToolResolver
from llm.core.agents import AgentDefinition
from llm.core.agent_views import AgentDefinitions


class CapabilityOwnerTests(unittest.TestCase):
    def test_canonical_identifiers_and_requested_exports_remain_open(self):
        self.assertEqual(ToolRegistry.CAPABILITY, "tools")
        self.assertEqual(COMPLETION_PROCESSORS, "completion_processors")
        self.assertEqual(AgentDefinition.CAPABILITY, "agents")
        calls = []
        opaque = object()
        class Components:
            def resolve(self, project, capability, *, data_factory=None):
                calls.append(capability)
                return () if capability == "completion_processors" else (opaque,)
        resolver = ComponentToolResolver(Components())
        self.assertEqual(resolver.resolve(object(), ("application.extension", "completion_processors")),
                         {"application.extension": (opaque,), "completion_processors": ()})
        self.assertEqual(calls, ["application.extension", "completion_processors"])
        self.assertNotIn("tools", calls)
        self.assertNotIn("agents", calls)

    def test_agent_projection_wire_selection_and_mutable_inputs_are_detached(self):
        original = {"worker": {"engine": "loop", "purpose": "original"}}
        class Agents:
            def list(self, project):
                return original
        resolver = ComponentToolResolver(SimpleNamespace(), agents=Agents())
        values = resolver.resolve(object(), ("agents",))
        self.assertEqual(values, {"agents": ({"records": original},)})
        selected = AgentDefinitions.selected(values["agents"], "worker")
        selected["purpose"] = "changed"
        self.assertEqual(values["agents"][0]["records"], original)
        values["agents"][0]["records"]["worker"]["purpose"] = "returned change"
        self.assertEqual(original["worker"]["purpose"], "original")
        for sources in ((), ({"records": {}},), ({"records": original}, {"records": original})):
            with self.subTest(sources=sources), self.assertRaisesRegex(ValueError, "must resolve to exactly one definition"):
                AgentDefinitions.selected(sources, "worker")

    def test_missing_or_non_json_agent_source_is_never_a_default_definition(self):
        cases = ({}, {"records": None}, {"records": []},
                 {"records": {"worker": {"engine": "loop", "tools": ("tuple",)}}})
        for source in cases:
            before = deepcopy(source)
            with self.subTest(source=source), self.assertRaises((KeyError, TypeError, ValueError)):
                AgentDefinitions.selected((source,), "worker")
            self.assertEqual(source, before)
