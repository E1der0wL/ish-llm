"""Independent quota, receipt and wire assertions for all usage consumers."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from llm.components.usage import ModelUsageRecord
from llm.core.models import ProjectConfig
from llm.core.policies import UsagePolicy
from llm.core.usage import TokenUsage, UsageReceipt, UsageSource, UsageSummary, UsageCall
from llm.policies import ExecutionLimitError
from llm.services.runtime.usage import check_admission


class UsageOwnershipTests(unittest.TestCase):
    def test_policy_view_does_not_materialize_omission_or_null(self):
        for value in ({}, {"usage": {}}, {"usage": {"max_calls": None}},
                      {"usage": {"project_max_calls": 2, "max_tokens": 10, "counter": "words"}}):
            with self.subTest(value=value):
                config = ProjectConfig(policies=value)
                original = deepcopy(config.to_dict())
                policy = UsagePolicy.from_policies(config.policies)
                self.assertEqual(policy.project.max_calls, value.get("usage", {}).get("project_max_calls"))
                self.assertEqual(config.to_dict(), original)
        with self.assertRaises(KeyError):
            UsagePolicy.from_run(SimpleNamespace(metadata={}))

    def test_quota_scopes_preserve_diagnostic_names_and_original_reservation(self):
        receipt = UsageReceipt.from_record({"started_at": "2026-10-23T00:00:00+00:00",
            "usage": {}, "usage_complete": False, "reserved_tokens": 7})
        cases = [({"max_calls": 1}, "max_calls"), ({"project_max_calls": 1}, "project_max_calls"),
                 ({"max_tokens": 9}, "max_tokens"), ({"project_max_tokens": 9}, "project_max_tokens")]
        for settings, label in cases:
            with self.subTest(settings=settings), self.assertRaisesRegex(ExecutionLimitError, label + " exhausted"):
                check_admission(UsagePolicy.from_dict(settings), 3, [receipt], [receipt])
        self.assertEqual(receipt.reserved_tokens, 7)
        self.assertFalse(receipt.usage_complete)

    def test_unknown_usage_blocks_tokens_but_never_resets_call_count(self):
        unknown = UsageReceipt.from_record({"usage_complete": False})
        self.assertIsNone(unknown.amount)
        for settings in ({"max_tokens": 100}, {"project_max_tokens": 100}):
            with self.subTest(settings=settings), self.assertRaises(ExecutionLimitError) as caught:
                check_admission(UsagePolicy.from_dict(settings), 1, [unknown], [unknown])
            self.assertEqual(caught.exception.code, "usage_unknown")
        with self.assertRaises(ExecutionLimitError) as caught:
            check_admission(UsagePolicy.from_dict({"max_calls": 1}), None, [], [unknown])
        self.assertEqual(caught.exception.code, "usage_limit")

    def test_token_observation_does_not_share_nested_data(self):
        raw = {"total_tokens": 2, "detail": {"cached": 1}}
        value = TokenUsage(raw)
        raw["total_tokens"] = 99
        raw["detail"]["cached"] = 99
        detached = value.to_dict()
        detached["detail"]["cached"] = 88
        self.assertEqual(value.to_dict(), {"total_tokens": 2, "detail": {"cached": 1}})
        for total in (True, -1, "2", None):
            with self.subTest(total=total), self.assertRaises(ValueError):
                TokenUsage({"total_tokens": total}).validate(complete=True)

    def test_terminal_receipt_preserves_identity_reservation_and_unknown_usage(self):
        raw = {"schema_version": 1, "id": "receipt", "project_id": "project", "component": "memory",
            "operation": "completion", "model": "test", "session_id": "session", "run_id": "run",
            "started_at": "2026-10-23T00:00:00+00:00", "ended_at": None, "status": "started",
            "usage": {}, "usage_complete": False, "reserved_tokens": 7, "error": None, "diagnostic_code": None}
        record = ModelUsageRecord.from_dict(raw)
        terminal = record.finish(ended_at="2026-10-23T00:00:01+00:00", usage={"total_tokens": 4})
        self.assertEqual(terminal.to_dict(), {**raw, "ended_at": "2026-10-23T00:00:01+00:00",
            "status": "completed", "usage": {"total_tokens": 4}, "usage_complete": True})
        failed = record.finish(ended_at="2026-10-23T00:00:01+00:00", usage={}, diagnostic_code="cancelled")
        self.assertEqual(failed.reserved_tokens, 7)
        self.assertFalse(failed.usage_complete)
        self.assertEqual(failed.error, "cancelled")
        self.assertEqual(record.to_dict(), raw)
        with self.assertRaises(ValueError):
            terminal.finish(ended_at="2026-10-23T00:00:02+00:00", usage={"total_tokens": 1})

    def test_summary_preserves_extension_receipts_and_period_boundaries(self):
        original = {"started_at": "2026-10-23T00:00:00+00:00", "usage": {},
            "usage_complete": False, "reserved_tokens": 7, "extension": {"source": "remote"}}
        call = UsageCall.for_component(original)
        result = UsageSummary.collect([call], UsagePolicy.from_dict({"period_seconds": 60}),
            at=datetime(2026, 10, 23, 0, 1, tzinfo=timezone.utc)).to_dict()
        self.assertEqual(result, {"calls": [{**original, "source": "component"}], "call_count": 1,
            "known_tokens": 0, "reserved_tokens": 7, "unknown_calls": 0, "period_seconds": 60})
        result["calls"][0]["extension"]["source"] = "changed"
        self.assertEqual(call.to_dict()["extension"], {"source": "remote"})

    def test_source_wire_is_exact_and_detached(self):
        raw = {"project_id": "p", "session_id": "s", "run_id": "r"}
        source = UsageSource.from_dict(raw)
        raw["project_id"] = "changed"
        self.assertEqual(source.to_dict(), {"project_id": "p", "session_id": "s", "run_id": "r"})
        with self.assertRaises(ValueError):
            UsageSource.from_dict({"project_id": "p", "session_id": "s"})

    def test_one_query_uses_one_period_boundary_for_equal_timestamps(self):
        from unittest.mock import patch
        call = UsageCall.for_component({"started_at": "2026-10-23T00:00:00+00:00",
            "usage_complete": False, "reserved_tokens": 2})
        with patch("llm.core.usage.datetime") as clock:
            clock.now.side_effect = [datetime(2026, 10, 23, 0, 1, tzinfo=timezone.utc),
                                     datetime(2026, 10, 23, 0, 2, tzinfo=timezone.utc)]
            clock.fromisoformat.side_effect = datetime.fromisoformat
            result = UsageSummary.collect([call, call], UsagePolicy.from_dict({"period_seconds": 60}))
        self.assertEqual(result.call_count, 2)
        self.assertEqual(result.reserved_tokens, 4)


if __name__ == "__main__":
    unittest.main()
