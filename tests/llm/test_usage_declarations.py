"""Reservation field stability and effect-free invalid token counter checks."""

from copy import deepcopy
from dataclasses import fields
from pathlib import Path
import tempfile
import unittest

from llm.components.base import Component
from llm.components.usage import ModelUsageRecord
from llm.core.policies import UsagePolicy
from llm.llm import LargeLanguageModel
from llm.policies import ExecutionLimitError
from llm.providers.observations import ModelCall, observed_call
from llm.services.runtime.usage import UsageScope


class Meter(Component):
    name = directory = "meter"


class UsageDeclarationTests(unittest.TestCase):
    def test_reservation_identity_is_declared_on_the_exact_immutable_fields(self):
        immutable = {item.name for item in fields(ModelUsageRecord) if item.metadata.get("reservation")}
        self.assertEqual(immutable, {"schema_version", "id", "project_id", "component", "operation", "model",
                                    "session_id", "run_id", "started_at", "reserved_tokens"})
        self.assertEqual(ModelUsageRecord.filename("receipt"), "receipt.json")

    def test_component_scope_preserves_project_limits_counter_and_original_settings(self):
        raw = {"counter": "chosen", "max_calls": 1, "max_tokens": 5,
               "project_max_calls": 2, "project_max_tokens": 10, "period_seconds": 60}
        before = deepcopy(raw)
        policy = UsagePolicy.from_dict(raw)
        component = policy.without_run_limits()
        self.assertFalse(component.run.active)
        self.assertTrue(component.project.active)
        self.assertEqual((component.project_max_calls, component.project_max_tokens, component.period_seconds,
                          component.counter), (2, 10, 60, "chosen"))
        self.assertEqual((policy.max_calls, policy.max_tokens), (1, 5))
        self.assertEqual(raw, before)


class ReservationEffectTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_requires_exact_nonnegative_integer_and_unlimited_scope_does_not_count(self):
        calls = []
        chosen = object()
        for count in (True, -1, 1.0, "1", None, 0, 2):
            with self.subTest(count=count):
                def measure(counter):
                    calls.append(counter)
                    return count
                invocation = ModelCall("test", None, measure, lambda _: {}, lambda _: "test_error")
                scope = UsageScope(UsagePolicy(max_tokens=10), chosen)
                before = len(calls)
                if type(count) is int and count >= 0:
                    self.assertEqual(await scope.reservation(invocation), count)
                else:
                    with self.assertRaises(ExecutionLimitError) as caught:
                        await scope.reservation(invocation)
                    self.assertEqual((caught.exception.code, str(caught.exception)),
                                     ("usage_configuration", "Token counter must return a nonnegative integer"))
                self.assertEqual(calls[before:], [chosen])
        invocation = ModelCall("test", None, lambda _: self.fail("no implicit reservation"),
                               lambda _: {}, lambda _: "test_error")
        self.assertIsNone(await UsageScope(UsagePolicy(), chosen).reservation(invocation))

    async def test_component_invalid_counter_preserves_receipts_and_never_calls_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "effect"
            async with LargeLanguageModel(root, components=[Meter()]) as app:
                app.policy_resolver.token_counters["chosen"] = lambda _: 0
                project = await app.projects.acreate(components=["meter"],
                    config={"policies": {"usage": {"project_max_tokens": 10, "counter": "chosen"}}})
                handle = app.project_manager.component(project.data, "meter")
                usage_root = project.paths.root / "meter/usage"
                before = {p.relative_to(usage_root): p.read_bytes() for p in usage_root.rglob("*") if p.is_file()}
                async def call(**request):
                    marker.write_text("called")
                    return {}
                for count in (True, -1, 1.0, "1", None):
                    with self.subTest(count=count):
                        invocation = ModelCall("test", None, lambda _: count, lambda _: {}, lambda _: "test_error")
                        with handle.model_scope(), self.assertRaises(ExecutionLimitError) as caught:
                            await observed_call(invocation, {}, call)
                        self.assertEqual(caught.exception.code, "usage_configuration")
                        self.assertFalse(marker.exists())
                        self.assertEqual({p.relative_to(usage_root): p.read_bytes()
                            for p in usage_root.rglob("*") if p.is_file()}, before)
                self.assertEqual(await project.components.meter.amodel_usage(), [])
