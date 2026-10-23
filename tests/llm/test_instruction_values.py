"""Instruction receipt states and immutable source evidence at Reader/Writer boundaries."""

from copy import deepcopy
import unittest

from llm.core.models import Message, MessageRole, MessageStatus
from llm.core.steering import RunInstruction
from llm.core.steering import SteeringRoute, SteeringTarget, SteeringMode, InstructionDataError
from llm.core.instruction_records import InstructionMessageRecord, InstructionReceipt
from llm.services.history.context import ConversationContextBuilder
from tests.llm.support.admission import admitted_message, uid


class InstructionValueTests(unittest.TestCase):
    def test_new_receipt_wire_preserves_unbound_nulls_and_omitted_aggregate_fields(self):
        route = SteeringRoute(("flow",), "agent", "loop")
        receipt = InstructionReceipt.reserve("reservation", route)
        record = InstructionMessageRecord.create("run", "input", (receipt,))
        expected = {"run_id": "run", "input_message_id": "input", "status": "pending", "targets": [
            {"id": "reservation", "scope": None, "status": "pending", "applications": [],
             "reservation": {"workflow_path": ["flow"], "node_id": "agent", "engine": "loop"}, "execution_id": None}]}
        self.assertEqual(record.to_dict(), expected)
        self.assertEqual(record.metadata(), {"steering": expected})
        self.assertNotIn("applications", record.to_dict())
        self.assertNotIn("reason", record.to_dict())
        self.assertFalse(receipt.restores_context)

    def test_reservation_transition_and_resume_keep_one_original_application(self):
        route = SteeringRoute(("flow",), "agent", "loop")
        receipt = InstructionReceipt.reserve("reservation", route)
        bound = receipt.bind(route, "scope")
        target = SteeringTarget("execution", "run", "loop", SteeringMode.CONSUME, scope="scope", accepting=True)
        activated = bound.activate(target)
        applied = activated.apply("run", "boundary", "execution", "step", "2026-10-23T00:00:00+00:00")
        self.assertEqual(applied.to_dict()["applications"], [{"run_id": "run", "boundary": "boundary",
            "target_id": "execution", "step_id": "step", "time": "2026-10-23T00:00:00+00:00"}])
        self.assertEqual(applied.apply("other-run", "other-boundary", "other-execution", None, "later").to_dict(),
                         applied.to_dict())
        self.assertEqual(receipt.to_dict()["applications"], [])
        self.assertIsNone(receipt.scope)
        self.assertTrue(applied.restores_context)
        with self.assertRaises(InstructionDataError):
            bound.bind(route, "other-scope")
        with self.assertRaises(InstructionDataError):
            applied.finish("cancelled")

    def test_missing_receipts_and_inconsistent_aggregate_fail_without_repair(self):
        original = self.partial_message().metadata["steering"]
        for transform in (lambda value: value.pop("targets"),
                          lambda value: value["targets"][0].pop("applications"),
                          lambda value: value.update(status="applied"),
                          lambda value: value.update(applications=[])):
            broken = deepcopy(original)
            transform(broken)
            before = deepcopy(broken)
            with self.subTest(broken=broken), self.assertRaises(InstructionDataError):
                InstructionMessageRecord(broken)
            self.assertEqual(broken, before)
        record = InstructionMessageRecord(original)
        emitted = record.to_dict()
        emitted["targets"][0]["applications"][0]["run_id"] = "changed"
        self.assertEqual(record.to_dict(), original)

    def partial_message(self):
        application = {"run_id": "run", "boundary": "input-a", "target_id": "a", "step_id": None,
                       "time": "2026-10-23T00:00:00+00:00"}
        return Message("instruction", MessageRole.USER, "node-local input", MessageStatus.QUEUED, run_id="run",
            metadata={"steering": {"run_id": "run", "input_message_id": "input", "status": "pending",
                "targets": [{"id": "a", "scope": "a", "status": "applied", "applications": [application]},
                            {"id": "b", "scope": "b", "status": "pending", "applications": []}],
                "applications": [application]}})

    def test_clone_pending_targets_keeps_applied_evidence_and_valid_aggregate(self):
        message = self.partial_message()
        original = deepcopy(message)
        RunInstruction.validate_message(message)
        clone, = ConversationContextBuilder().for_clone([message])
        view = RunInstruction.from_message(clone)
        self.assertEqual(view.status, "partially_applied")
        self.assertEqual(clone.status, "committed")
        self.assertEqual([target["status"] for target in view.targets], ["applied", "unapplied"])
        self.assertEqual(view.applications, original.metadata["steering"]["applications"])
        self.assertEqual(view.targets[1]["reason"], "cloned")
        self.assertEqual(message, original)
        followup = admitted_message(uid("next request"), "user", "queued")
        context = ConversationContextBuilder().for_run([clone, followup], followup.id)
        self.assertEqual([item.id for item in context], [followup.id])
