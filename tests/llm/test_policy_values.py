"""Independent policy wire, omission, copy and required Run evidence checks."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from llm.core.policies import ApprovalPolicy, ToolPolicyValues, ToolRetryPolicy, normalize_policies
from llm.core.tool_policy_binding import ToolExecutionBinding
from llm.services.runtime.policies import TokenCounters
from llm.services.runtime.tools import ToolExecutionScope, ToolRuntime


class PolicyValueTests(unittest.TestCase):
    def test_omitted_saved_values_remain_absent_and_views_do_not_edit_settings(self):
        raw = {"tools": {"allowed_tools": ["write"], "argument_constraints": {
            "write": {"path": {"mode": "fixed", "value": {"selected": ["a"]}}}}}}
        before = deepcopy(raw)
        value = ToolPolicyValues.from_policies(raw)
        value.allowed_tools.append("other")
        value.argument_constraints["write"]["path"]["value"]["selected"].append("b")
        self.assertEqual(raw, before)
        self.assertEqual(normalize_policies(raw), before)
        self.assertEqual(normalize_policies({}), {})
        self.assertIsNone(ToolRetryPolicy.from_dict({}).max_retries)
        self.assertIsNone(ToolRetryPolicy.from_dict({}).delay_seconds)

    def test_approval_values_preserve_order_and_reject_duplicate_or_unknown_evidence(self):
        raw = {"enabled": True, "risk_scheme": "host", "rules": [
            {"id": "first", "category": "files", "max_risk": 0},
            {"id": "second", "category": "files", "max_risk": 1}]}
        before = deepcopy(raw)
        policy = ApprovalPolicy.from_dict(raw)
        self.assertEqual([(r.id, r.category, r.max_risk) for r in policy.rules],
                         [("first", "files", 0), ("second", "files", 1)])
        self.assertEqual(policy.to_dict(), before)
        raw["rules"][0]["max_risk"] = 5
        self.assertEqual(policy.rules[0].max_risk, 0)
        for invalid in ({**before, "rules": [before["rules"][0]] * 2},
                        {"rules": before["rules"]}, {**before, "unknown": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ApprovalPolicy.from_dict(invalid)

    def test_run_policy_projection_requires_admitted_snapshot(self):
        with self.assertRaises(KeyError):
            ToolRetryPolicy.from_run(SimpleNamespace(metadata={}))
        run = SimpleNamespace(metadata={"policies": {"tool_retry": {"max_retries": 2}}})
        self.assertEqual(ToolRetryPolicy.from_run(run).max_retries, 2)
        self.assertEqual(run.metadata, {"policies": {"tool_retry": {"max_retries": 2}}})

    def test_tool_binding_wire_and_detached_constraints_are_unchanged(self):
        rule = {"write": {"path": {"mode": "fixed", "value": ["a"]}}}
        scope = ToolExecutionScope(ToolRuntime(), policy={"max_calls": 2, "allowed_tools": ["write"],
            "argument_constraints": rule}, retry={"max_retries": 1, "delay_seconds": 0})
        expected = {"revision": "1", "allowed_tools": ["write"], "max_calls": 2, "approval": False,
            "timeout_seconds": None, "max_output_chars": None, "classifier": False, "isolated_runner": False,
            "operation_key": False, "retry_safe_tools": [], "max_retries": 1, "retry_delay": 0,
            "argument_constraints": deepcopy(rule)}
        self.assertEqual(scope.binding(), expected)
        value = scope.binding()
        value["argument_constraints"]["write"]["path"]["value"].append("changed")
        value["allowed_tools"].append("other")
        self.assertEqual(scope.binding(), expected)
        selected_constraints = ToolExecutionBinding.constraints(scope.binding())
        selected_constraints["write"]["path"]["value"].append("projection change")
        self.assertEqual(scope.argument_constraints, rule)
        runtime = ToolRuntime(decide_retry=lambda *args: None)
        selected = ToolExecutionScope(runtime).binding()
        self.assertIs(selected["retry_selector"], True)
        self.assertNotIn("retry_selector", scope.binding())

    def test_counter_registry_retains_explicit_override_and_independent_maps(self):
        count = lambda request: 7
        selected = {"model_default": count, "custom": count}
        counters = TokenCounters.build(selected)
        self.assertIs(counters["model_default"], count)
        counters.pop("custom")
        self.assertIn("custom", selected)
        self.assertIn("model_default", TokenCounters.build())
        for invalid in ([], {"bad": None}, {" ": count}):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                TokenCounters.build(invalid)
