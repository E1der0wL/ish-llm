"""Transfer values preserve required witnesses and the Repository CAS boundary."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest

from llm.core.models import new_id
from llm.services.infrastructure.storage import revision_token
from llm.services.runtime.transfer_values import (
    TransferAction, TransferAuthorization, TransferContent, TransferDirection,
    TransferIdentity, TransferIntent, TransferInventory, TransferReceipt, TransferSource,
)


class TransferValueTests(unittest.TestCase):
    def setUp(self):
        self.project, self.source, self.target, self.message = [new_id() for _ in range(4)]
        self.intent = TransferIntent(1, TransferIdentity.create(self.project, self.source, "key"),
            TransferSource.host(self.project, self.source), self.target, ("writer",), "key",
            TransferContent.digest("original"), None, (self.source, self.target))
        self.receipt = TransferReceipt.accept(self.intent, self.message, "2026-10-23T00:00:00+00:00")

    def test_receipt_has_exact_original_wire_fields_and_explicit_host_nulls(self):
        expected = {"schema_version": 1, "id": revision_token([self.project, self.source, "key"]),
                    "source": {"project_id": self.project, "session_id": self.source,
                               "run_id": None, "agent_id": None, "message_id": None},
                    "target_session_id": self.target, "recipients": ["writer"], "key": "key",
                    "content_digest": "0682c5f2076f099c34cfdd15a9e063849ed437a49677e6fcc5b4198c76575be5",
                    "deadline": None, "chain": [self.source, self.target],
                    "target_message_id": self.message, "accepted_at": "2026-10-23T00:00:00+00:00"}
        expected["revision"] = revision_token(expected)
        self.assertEqual(self.receipt.to_dict(), expected)
        self.assertEqual(TransferReceipt.read(expected), self.receipt)
        self.assertNotIn("target_message_id", self.intent.to_dict())
        with self.assertRaises(ValueError):
            TransferReceipt.read(self.intent.to_dict())

    def test_receipt_projections_cannot_change_identity_or_nested_payload(self):
        original = self.receipt.to_dict()
        value = self.receipt.to_dict()
        value["source"]["project_id"] = new_id()
        value["recipients"].append("other")
        self.receipt.metadata()["transfer"]["chain"].clear()
        with self.assertRaises(FrozenInstanceError):
            self.receipt.source.run_id = new_id()
        with self.assertRaises(FrozenInstanceError):
            self.receipt.recipients += ("other",)
        self.assertEqual(self.receipt.to_dict(), original)
        with self.assertRaisesRegex(ValueError, "revision"):
            TransferReceipt.read(value)

    def test_ledger_update_retains_original_cas_revision_without_changing_reader_evidence(self):
        ledger = TransferInventory.empty(project_id=self.project, session_id=self.source)
        before = ledger.to_dict()
        proposal = ledger.with_receipt(TransferDirection.OUTGOING, self.receipt)
        self.assertEqual(proposal["revision"], before["revision"])
        ledger.require_update(proposal)
        with self.assertRaises(ValueError):
            TransferInventory.read(proposal, project_id=self.project, session_id=self.source)
        committed = TransferInventory.committed(proposal, project_id=self.project, session_id=self.source)
        self.assertNotEqual(committed.revision, ledger.revision)
        self.assertEqual(committed.entries(TransferDirection.OUTGOING), {self.receipt.id: self.receipt})
        committed.entries(TransferDirection.OUTGOING).clear()
        self.assertEqual(ledger.to_dict(), before)
        self.assertEqual(len(committed.entries(TransferDirection.OUTGOING)), 1)
        with self.assertRaisesRegex(ValueError, "changed"):
            committed.require_update(proposal)
        with self.assertRaises(ValueError):
            ledger.with_receipt(TransferDirection.INCOMING, self.receipt)
        self.assertEqual(ledger.to_dict(), before)

    def test_required_source_fields_and_missing_ledger_entries_are_not_repaired(self):
        for name in self.receipt.to_dict()["source"]:
            value = self.receipt.to_dict()
            del value["source"][name]
            value["revision"] = revision_token({k: v for k, v in value.items() if k != "revision"})
            before = deepcopy(value)
            with self.subTest(field=name), self.assertRaises(ValueError):
                TransferReceipt.read(value)
            self.assertEqual(value, before)
        ledger = TransferInventory.empty(project_id=self.project, session_id=self.source).to_dict()
        for name in ledger:
            damaged = {k: v for k, v in ledger.items() if k != name}
            with self.subTest(field=name), self.assertRaises(ValueError):
                TransferInventory.read(damaged, project_id=self.project, session_id=self.source)

    def test_exact_version_type_precedes_the_current_version_comparison(self):
        for version in (True, False, 1.0, None, "1", 2):
            value = self.receipt.to_dict()
            value["schema_version"] = version
            value["revision"] = revision_token({k: v for k, v in value.items() if k != "revision"})
            before = deepcopy(value)
            with self.subTest(version=version), self.assertRaises((TypeError, ValueError)):
                TransferReceipt.read(value)
            self.assertEqual(value, before)
        self.assertEqual(TransferReceipt.read(self.receipt.to_dict()).schema_version, 1)

    def test_authorization_and_clone_are_detached_projections_without_execution_authority(self):
        payload = TransferAuthorization(TransferAction.SEND, self.project, self.source, self.target,
                                        self.intent.to_dict()).to_dict()
        self.assertEqual(payload["action"], "send")
        self.assertNotIn("target_message_id", payload["receipt"])
        payload["receipt"]["source"]["project_id"] = new_id()
        self.assertEqual(self.intent.source.project_id, self.project)
        original = {**self.receipt.metadata(), "request_context": {"enabled": False}}
        clone = TransferReceipt.clone_metadata(original)
        self.assertEqual(clone, {"cloned_transfer": {"id": self.receipt.id, "revision": self.receipt.revision},
                                 "request_context": {"enabled": False}})
        self.assertFalse(TransferReceipt.present(clone))
        self.assertEqual(TransferReceipt.from_metadata(original), self.receipt)
        with self.assertRaises(ValueError):
            TransferReceipt.read(clone["cloned_transfer"])
