"""Admission projections must not mutate or replace execution evidence."""

from copy import deepcopy
import unittest

from llm.core.agents import AgentBinding
from llm.core.models import Message, MessageRole, MessageStatus, new_id
from llm.services.infrastructure.storage import revision_token
from llm.services.runtime.requests import RequestAdmission


class RequestValueTests(unittest.TestCase):
    def setUp(self):
        self.project, self.session, self.message_id = new_id(), new_id(), new_id()
        binding = AgentBinding.create(self.project, "writer", {"engine": "base"})
        self.raw = {"schema_version": 1, "project_id": self.project, "session_id": self.session,
                    "message_id": self.message_id, "recipients": ["writer"],
                    "bindings": {"writer": binding.to_dict()}, "dispatch": None}

    def test_typed_admission_keeps_independent_detached_wire_evidence(self):
        expected = deepcopy(self.raw)
        value = RequestAdmission.validate(self.raw)
        self.raw["bindings"]["writer"]["definition"]["engine"] = "changed"
        value.recipients.append("other")
        value.binding("writer").definition["engine"] = "changed"
        value.metadata()["admission"]["recipients"].clear()
        self.assertEqual(value.to_dict(), expected)
        self.assertEqual((value.project_id, value.session_id, value.message_id),
                         (self.project, self.session, self.message_id))
        self.assertEqual(value.binding("writer").engine, "base")
        self.assertIsNone(value.dispatched)

    def test_dispatch_history_and_map_cannot_be_changed_through_projections(self):
        value = RequestAdmission.validate(self.raw)
        message = Message(self.message_id, MessageRole.USER, "original", MessageStatus.COMMITTED,
                          metadata={"admission": deepcopy(self.raw), "request_context": None,
                                    "extension": {"empty": [], "enabled": False, "ratio": 1.0}})
        original = deepcopy(message)
        run_id = new_id()
        result = RequestAdmission.dispatch(value, run_ids={"writer": run_id}, history=[message])
        wire = result.to_dict()
        self.assertEqual(message, original)
        self.assertEqual(wire["dispatch"]["run_ids"], {"writer": run_id})
        self.assertEqual(wire["dispatch"]["history_revision"], revision_token(wire["dispatch"]["history"]))
        self.assertEqual(wire["dispatch"]["history"][0]["metadata"],
                         {"request_context": None, "extension": {"empty": [], "enabled": False, "ratio": 1.0}})
        result.dispatched.history[0].metadata.clear()
        result.dispatched.history[0].content = "changed"
        result.dispatched.run_ids["writer"] = new_id()
        self.assertEqual(result.to_dict(), wire)
        self.assertEqual(result.dispatched.history[0].content, "original")
        self.assertIsNone(value.dispatched)
        damaged = deepcopy(wire)
        damaged["dispatch"]["history"][0]["metadata"]["extension"]["ratio"] = 1
        with self.assertRaisesRegex(ValueError, "revision"):
            RequestAdmission.validate(damaged)

    def test_missing_dispatch_is_not_the_explicit_undispatched_state(self):
        for name in self.raw:
            damaged = deepcopy(self.raw)
            del damaged[name]
            before = deepcopy(damaged)
            with self.subTest(field=name), self.assertRaises(ValueError):
                RequestAdmission.validate(damaged)
            self.assertEqual(damaged, before)
        message = Message(self.message_id, MessageRole.USER, "original", MessageStatus.QUEUED,
                          metadata={"admission": deepcopy(self.raw)})
        self.assertTrue(RequestAdmission.present(message))
        self.assertIsNone(RequestAdmission.read(message, project_id=self.project,
                                              session_id=self.session).dispatched)
        message.metadata["admission"] = None
        with self.assertRaises(ValueError):
            RequestAdmission.read(message, project_id=self.project, session_id=self.session)

    def test_clone_retains_provenance_without_an_executable_admission(self):
        message = Message(self.message_id, MessageRole.USER, "original", MessageStatus.COMMITTED,
                          metadata={"admission": deepcopy(self.raw), "request_context": {"enabled": False}})
        message.metadata["admission"] = RequestAdmission.dispatch(RequestAdmission.validate(self.raw),
            run_ids={"writer": new_id()}, history=[message]).to_dict()
        before = deepcopy(message)
        clone = RequestAdmission.clone_message(message)
        self.assertEqual(message, before)
        self.assertEqual(clone.metadata, {"request_context": {"enabled": False}, "cloned_request": {
            "project_id": self.project, "session_id": self.session, "message_id": self.message_id,
            "recipients": ["writer"]}})
        self.assertFalse(RequestAdmission.present(clone))
        self.assertEqual(RequestAdmission.run_ids(clone), ())
        with self.assertRaises(ValueError):
            RequestAdmission.read(clone, project_id=self.project, session_id=self.session)
