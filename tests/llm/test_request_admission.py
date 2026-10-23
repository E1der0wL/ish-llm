"""Recipient admission corruption must never become a fresh executable request."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from llm.core.agents import AgentBinding
from llm.core.models import Message, MessageRole, MessageStatus, new_id
from llm.services.infrastructure.storage import revision_token
from llm.services.runtime.requests import RequestAdmission


class RequestAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.project, self.session, self.message_id = new_id(), new_id(), new_id()
        self.bindings = {identifier: AgentBinding.create(self.project, identifier,
                         {"engine": "base", "tools": [], "system_prompt": None}).to_dict()
                         for identifier in ("writer", "reader")}
        self.value = RequestAdmission.create(project_id=self.project, session_id=self.session,
                    message_id=self.message_id, bindings=self.bindings).to_dict()
        self.message = Message(self.message_id, MessageRole.USER, "request", MessageStatus.QUEUED,
                               metadata={"admission": deepcopy(self.value)})
        self.owner = {"project_id": self.project, "session_id": self.session}

    def dispatch(self):
        original = deepcopy(self.message)
        self.message.status = MessageStatus.COMMITTED
        ids = {identifier: new_id() for identifier in self.bindings}
        result = RequestAdmission.dispatch(RequestAdmission.validate(self.value), run_ids=ids, history=[self.message]).to_dict()
        self.assertEqual(self.message.metadata["admission"], original.metadata["admission"])
        self.assertIsNone(self.value["dispatch"])
        self.message.metadata["admission"] = result
        runs = [SimpleNamespace(id=ids[identifier], session_id=self.session,
                input_message_id=self.message_id, assistant_message_id=new_id(), agent_id=identifier,
                engine=binding["engine"], metadata={"agent_binding": deepcopy(binding)})
                for identifier, binding in self.bindings.items()]
        return result, runs

    def test_only_explicit_all_snapshots_validated_session_members(self):
        members = ["writer", "reader"]
        selected = RequestAdmission.select("all", members)
        members.append("later")
        self.assertEqual(selected, ["reader", "writer"])
        self.assertEqual(RequestAdmission.select(["writer"], members), ["writer"])
        for selection in (None, "writer", [], {}, True, ["other"], ["writer", "writer"]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                RequestAdmission.select(selection, members)
        with self.assertRaises(ValueError):
            RequestAdmission.select("all", [])

    def test_admission_requires_every_field_exact_version_and_binding(self):
        invalid = [{k: v for k, v in self.value.items() if k != name} for name in self.value]
        invalid += [{**self.value, "schema_version": version} for version in (True, 1.0, "1", None, 2)]
        invalid += [None, {}, {**self.value, "exclusive": False}, {**self.value, "recipients": []},
                    {**self.value, "recipients": ["writer", "reader"]},
                    {**self.value, "bindings": {"reader": self.bindings["writer"]}},
                    {**self.value, "project_id": new_id()}, {**self.value, "session_id": "../outside"}]
        for value in invalid:
            with self.subTest(value=value):
                before = deepcopy(value)
                with self.assertRaises((ValueError, TypeError)):
                    RequestAdmission.validate(value)
                self.assertEqual(value, before)
        self.bindings["writer"]["definition"]["tools"].append("changed")
        self.assertEqual(self.value["bindings"]["writer"]["definition"]["tools"], [])

    def test_only_original_user_identity_and_state_can_own_admission(self):
        self.assertEqual(RequestAdmission.read(self.message, **self.owner).to_dict(), self.value)
        self.assertEqual(RequestAdmission.runs(self.message, [], **self.owner), [])
        for name, value in (("id", new_id()), ("role", MessageRole.ASSISTANT),
                            ("run_id", new_id()), ("status", MessageStatus.COMMITTED), ("metadata", {})):
            message = deepcopy(self.message)
            setattr(message, name, value)
            with self.subTest(field=name), self.assertRaises(ValueError):
                RequestAdmission.read(message, **self.owner)
        with self.assertRaises(ValueError):
            RequestAdmission.read(self.message, project_id=new_id(), session_id=self.session)
        with self.assertRaises(ValueError):
            RequestAdmission.read(self.message, project_id=self.project, session_id=new_id())

    def test_dispatch_freezes_shared_history_without_recursive_envelopes(self):
        self.message.metadata["request_context"] = {"cwd": "", "enabled": False, "turn": 0}
        value, _ = self.dispatch()
        history = value["dispatch"]["history"]
        self.assertNotIn("admission", history[0]["metadata"])
        self.assertEqual(history[0]["metadata"]["request_context"], {"cwd": "", "enabled": False, "turn": 0})
        self.message.content = "later edit"
        self.assertEqual(history[0]["content"], "request")
        with self.assertRaises(ValueError):
            RequestAdmission.dispatch(RequestAdmission.validate(value), run_ids={}, history=[])

    def test_dispatch_rejects_lost_map_history_or_revision_without_repair(self):
        value, _ = self.dispatch()
        invalid = [{**value, "dispatch": {k: v for k, v in value["dispatch"].items() if k != name}}
                   for name in value["dispatch"]]
        for name, replacement in (("run_ids", {}), ("history", []), ("history_revision", "0" * 64)):
            invalid.append({**value, "dispatch": {**value["dispatch"], name: replacement}})
        duplicate = deepcopy(value)
        duplicate["dispatch"]["run_ids"]["reader"] = duplicate["dispatch"]["run_ids"]["writer"]
        invalid.append(duplicate)
        changed = deepcopy(value)
        changed["dispatch"]["history"][0]["content"] = "changed"
        invalid.append(changed)
        nested = deepcopy(value)
        nested["dispatch"]["history"][0]["metadata"]["admission"] = self.value
        nested["dispatch"]["history_revision"] = revision_token(nested["dispatch"]["history"])
        invalid.append(nested)
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                before = deepcopy(candidate)
                with self.assertRaises(ValueError):
                    RequestAdmission.validate(candidate)
                self.assertEqual(candidate, before)

    def test_all_runs_required_once_with_exact_owner_binding_and_context(self):
        _, runs = self.dispatch()
        self.assertEqual([run.agent_id for run in RequestAdmission.runs(self.message, runs, **self.owner)],
                         ["reader", "writer"])
        invalid = [[], runs[:1], [*runs, runs[0]]]
        for attribute, replacement in (("session_id", new_id()), ("engine", "different"),
                                       ("id", new_id()), ("assistant_message_id", runs[1].assistant_message_id)):
            mutated = deepcopy(runs)
            setattr(mutated[0], attribute, replacement)
            invalid.append(mutated)
        for metadata in ({}, {**runs[0].metadata, "request_context": None},
                         {"agent_binding": self.value["bindings"][runs[1].agent_id]}):
            mutated = deepcopy(runs)
            mutated[0].metadata = metadata
            invalid.append(mutated)
        for records in invalid:
            with self.subTest(records=records), self.assertRaises(ValueError):
                RequestAdmission.runs(self.message, records, **self.owner)

    def test_run_witness_prevents_reset_to_undispatched_state(self):
        _, runs = self.dispatch()
        self.message.metadata["admission"] = deepcopy(self.value)
        self.message.status = MessageStatus.QUEUED
        with self.assertRaisesRegex(ValueError, "Run evidence"):
            RequestAdmission.runs(self.message, runs, **self.owner)

    def test_single_recipient_has_the_same_group_contract(self):
        self.bindings = {"writer": self.bindings["writer"]}
        self.value = RequestAdmission.create(project_id=self.project, session_id=self.session,
                    message_id=self.message_id, bindings=self.bindings).to_dict()
        value, runs = self.dispatch()
        self.assertEqual(value["recipients"], ["writer"])
        self.assertIsNone(self.message.run_id)
        self.assertEqual(RequestAdmission.runs(self.message, runs, **self.owner), runs)


if __name__ == "__main__":
    unittest.main()
