"""Provider choices retain explicit absence and cannot erase capacity limits."""

from copy import deepcopy
import unittest
from unittest.mock import AsyncMock

from jsonschema import ValidationError
from llm.components.memory.processing import resolve_processing_config
from llm.core.models import ProjectConfig
from llm.engines.loop import LoopEngine
from llm.policies.retry import RetryPolicy
from llm.providers.calls import ProviderCalls, ProviderLimits, ProviderCapacityError
from llm.providers.requests import resolve_provider_options, invoke, ProviderError
from llm.services.runtime.policies import ProjectPolicyResolver


class ProviderChoicesTests(unittest.IsolatedAsyncioTestCase):
    def test_retry_calculation_does_not_materialize_missing_configuration(self):
        policy = RetryPolicy()
        self.assertEqual(policy.attempts, 1)
        self.assertIsNone(policy.max_attempts)
        self.assertFalse(policy.allows(1))
        self.assertEqual(RetryPolicy(3).attempts, 3)
        self.assertTrue(RetryPolicy(3).allows(2))
        self.assertFalse(RetryPolicy(3).allows(3))
        for invalid in (0, False, 1.0, -1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                RetryPolicy(invalid)

    def test_provider_options_require_an_object_before_copying(self):
        for value in (None, False, 0, "", [], (), [("max_attempts", 2)]):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                resolve_provider_options(value)
        options = {"max_attempts": 1, "wall_timeout": None, "delay_seconds": 0,
                   "max_delay_seconds": 0, "respect_retry_after": False, "jitter": False}
        before = deepcopy(options)
        resolved = resolve_provider_options(options)
        self.assertEqual(resolved, before)
        self.assertIsNot(resolved, options)
        self.assertEqual(resolve_provider_options({}), {})
        self.assertEqual(options, before)

    def test_memory_provider_null_clears_but_wrong_falsey_values_are_rejected(self):
        for value in (None, {}, {"max_attempts": 1, "delay_seconds": 0}):
            config = {"policy": {"processing": {"provider": value}}}
            before = deepcopy(config)
            self.assertEqual(resolve_processing_config(config)["provider"], value)
            self.assertEqual(config, before)
        self.assertNotIn("provider", resolve_processing_config({}))
        for value in (False, 0, "", [], (), [("max_attempts", 2)]):
            config = {"policy": {"processing": {"provider": value}}}
            before = deepcopy(config)
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                resolve_processing_config(config)
            self.assertEqual(config, before)

    def test_loop_keeps_missing_null_and_value_provenance(self):
        engine = LoopEngine()
        project = ProjectConfig({"parameters": {"engines": {
            "loop": {"policy": {"provider": {"max_attempts": 2, "delay_seconds": 0}}}}}})
        inherited = engine.resolve_config(project, "loop")
        self.assertEqual(inherited["values"]["policy"]["provider"]["max_attempts"], 2)
        session = {"parameters": {"engines": {"loop": {"policy": {"provider": None}}}}}
        before = deepcopy(session)
        # Null cannot remove a parent's explicit bound. Keep the existing
        # conservative narrowing contract while testing a legal policy clear.
        with self.assertRaisesRegex(ValueError, "widens parent limit"):
            engine.resolve_config(project, "loop", session_config=session)
        unbounded = ProjectConfig({"parameters": {"engines": {
            "loop": {"policy": {"provider": {"jitter": False}}}}}})
        cleared = engine.resolve_config(unbounded, "loop", session_config=session)
        self.assertIsNone(cleared["values"]["policy"]["provider"])
        self.assertEqual(cleared["sources"]["/policy/provider"], "session")
        self.assertEqual(session, before)
        self.assertEqual(engine.resolve_config(ProjectConfig(), "loop")["values"], {})

    async def test_capacity_rejects_bad_objects_and_preserves_zero_wait(self):
        for value in (False, 0, "", [], (), {}, {"max_active": 1}):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ProviderCalls(value)

        class FalseyLimits(ProviderLimits):
            def __bool__(self):
                return False

        limits = FalseyLimits(max_active=1, max_waiting=0, wait_seconds=0)
        calls = ProviderCalls(limits)
        self.assertIs(calls.limits, limits)
        await calls.acquire()
        try:
            with self.assertRaises(ProviderCapacityError):
                await calls.acquire()
            self.assertEqual(calls.stats, {"active": 1, "waiting": 0})
        finally:
            calls.release()
        self.assertEqual(calls.stats, {"active": 0, "waiting": 0})
        self.assertEqual(ProviderCalls(None).limits, ProviderLimits())

    def test_counter_registry_rejects_bad_objects_without_losing_falsey_mapping(self):
        for value in (False, 0, "", [], (), [("counter", lambda: 1)]):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ProjectPolicyResolver(value)

        class FalseyCounters(dict):
            def __bool__(self):
                return False

        def counter(**request):
            return 1
        registry = FalseyCounters(counter=counter)
        resolver = ProjectPolicyResolver(registry)
        self.assertIs(resolver.token_counters["counter"], counter)
        registry.clear()
        self.assertIs(resolver.token_counters["counter"], counter)
        self.assertEqual(set(ProjectPolicyResolver(None).token_counters), {"model_default"})

    async def test_invalid_policy_never_calls_provider_and_missing_only_calls_once(self):
        for options in ("", [], (), {"max_attempts": None}, {"max_attempts": False}):
            call = AsyncMock(return_value="unexpected")
            with self.subTest(options=options), self.assertRaises((TypeError, ValueError, ValidationError)):
                await invoke("test", {}, call, options)
            call.assert_not_awaited()
        for options, expected in (({}, 1), ({"max_attempts": 1}, 1), ({"max_attempts": 2}, 2)):
            call = AsyncMock(side_effect=ConnectionError("offline"))
            with self.subTest(options=options), self.assertRaises(ProviderError):
                await invoke("test", {}, call, options)
            self.assertEqual(call.await_count, expected)
