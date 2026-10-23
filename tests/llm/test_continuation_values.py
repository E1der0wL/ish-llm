"""Required continuation witnesses and unknown execution entry must stay distinct."""

from copy import deepcopy
import unittest

from llm.core.configuration import UNSET
from llm.core.continuations import (
    RestartEvidence, RestartRecord, ResumePlanEvidence, ResumeRecord, RunExecutionEvidence,
)
from llm.services.infrastructure.storage import revision_token


class ContinuationValueTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"run_id": "source", "checkpoint": "graph", "digest": "original-digest",
                    "retry_nodes": ["node"], "decisions": {"node": {"answer": [1]}},
                    "application": {"preserve": False}}

    def test_choices_and_child_projection_preserve_original_witnesses(self):
        before = deepcopy(self.raw)
        value = ResumeRecord.read(self.raw)
        self.raw["decisions"]["node"]["answer"].clear()
        value.decisions["node"]["answer"].append(2)
        value.metadata()["resume"]["retry_nodes"].clear()
        child = value.for_child(retry_nodes=[], decisions={"child": {"answer": None}})
        self.assertEqual(value.to_dict(), before)
        self.assertEqual(child.to_dict(), {**before, "retry_nodes": [], "decisions": {"child": {"answer": None}}})
        self.assertEqual((child.source_run_id, child.checkpoint, child.digest),
                         ("source", "graph", "original-digest"))

    def test_required_fields_are_not_replaced_by_fresh_empty_choices(self):
        required = ("run_id", "checkpoint", "digest", "retry_nodes", "decisions")
        for name in required:
            damaged = {key: value for key, value in self.raw.items() if key != name}
            before = deepcopy(damaged)
            for read in (ResumeRecord.read, lambda raw: ResumeRecord.decisions_for({"resume": raw}),
                         lambda raw: ResumeRecord.source_id({"resume": raw})):
                with self.subTest(field=name, reader=read), self.assertRaises(ValueError):
                    read(damaged)
            self.assertEqual(damaged, before)
        for raw in (None, {}, {**self.raw, "decisions": None}, {**self.raw, "retry_nodes": None}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ResumeRecord.from_metadata({"resume": raw})
        self.assertIsNone(ResumeRecord.from_metadata({}))
        self.assertEqual(ResumeRecord.decisions_for({}), {})
        self.assertEqual(ResumeRecord.retry_nodes_for({}), ())

    def test_partial_output_omission_and_explicit_false_are_not_rewritten(self):
        omitted = ResumeRecord.create("source", "loop", "digest", [], {})
        self.assertNotIn("partial_output", omitted.to_dict())
        self.assertFalse(omitted.partial_output)
        explicit = ResumeRecord.read({**omitted.to_dict(), "partial_output": False})
        self.assertFalse(explicit.partial_output)
        self.assertIs(explicit.to_dict()["partial_output"], False)
        self.assertIs(explicit.for_child(retry_nodes=[], decisions={}).to_dict()["partial_output"], False)
        for value in (None, 0, 1, "false"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ResumeRecord.read({**omitted.to_dict(), "partial_output": value})
        self.assertTrue(ResumeRecord.create("source", "loop", "digest", [], {}, partial_output=True).partial_output)

    def test_restart_requires_original_identity_and_digest_and_keeps_input_detached(self):
        raw = {"run_id": "source", "digest": "revision"}
        value = RestartRecord.read(raw)
        raw["digest"] = "changed"
        self.assertEqual(value.to_dict(), {"run_id": "source", "digest": "revision"})
        self.assertEqual(RestartRecord.create("source", "revision"), value)
        for damaged in ({}, {"run_id": "source"}, {"digest": "revision"}, {"run_id": None, "digest": "revision"}):
            with self.subTest(raw=damaged), self.assertRaises(ValueError):
                RestartRecord.read(damaged)

    def test_execution_entry_absence_never_proves_no_execution(self):
        missing = {}
        self.assertIs(RunExecutionEvidence.read(missing), UNSET)
        self.assertFalse(RunExecutionEvidence.never_started(missing))
        self.assertEqual(missing, {})
        admitted = RunExecutionEvidence.admission_metadata()
        self.assertEqual(admitted, {"execution_started": False})
        self.assertTrue(RunExecutionEvidence.never_started(admitted))
        RunExecutionEvidence.mark_started(admitted)
        self.assertEqual(admitted, {"execution_started": True})
        self.assertFalse(RunExecutionEvidence.never_started(admitted))
        for value in (None, 0, 1, "false", []):
            raw = {"execution_started": value}
            before = deepcopy(raw)
            with self.subTest(value=value), self.assertRaises(ValueError):
                RunExecutionEvidence.never_started(raw)
            self.assertEqual(raw, before)

    def test_revision_projection_preserves_wire_shape_and_raw_nested_values(self):
        run = {"id": "run", "metadata": {"amount": 1.0}}
        message = {"id": "message"}
        restart = RestartEvidence(run, message)
        self.assertEqual(restart.to_dict(), {"run": run, "input": message})
        self.assertEqual(revision_token(restart.to_dict()), revision_token({"run": run, "input": message}))
        expected = {"run": deepcopy(run), "checkpoint": None, "views": [], "project": {},
                    "session": {"id": "session"}, "messages": [message], "runs": [deepcopy(run)]}
        plan = ResumePlanEvidence(run, None, (), {}, {"id": "session"}, (message,), (run,))
        self.assertEqual(plan.to_dict(), expected)
        self.assertEqual(revision_token(plan.to_dict()), revision_token(expected))
        run["metadata"]["amount"] = 2
        self.assertEqual(plan.to_dict(), expected)
