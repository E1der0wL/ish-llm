"""Owner contracts preserve independent wire values and explicit host choices."""

import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from llm.core.models import ConversationStorage, Project, ResourceKind
from llm.core.contracts import ResourceRef
from llm.core.continuations import ResumeRecord, RestartRecord
from llm.core.checkpoints import CheckpointEnvelope, unwrap_record
from llm.core.paths import ProjectPaths
from llm.core.policies import ContextPolicy, RunPolicy, describe_policies, normalize_policies
from llm.llm import LargeLanguageModel
from llm.services.composition import BackendServices
from llm.services.history.conversation import ProjectConversations
from llm.services.infrastructure.journal import OutputJournal
from llm.services.runtime.runs import RunRepository


class OwnerDeclarationTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration_view_and_edit_preview_preserve_original_and_cas(self):
        from llm.services.schema import ProjectConfiguration
        original = {"project": {"id": "p", "config": {"data": {"saved": None}}},
                    "values": {"title": "original", "conversation_storage": None,
                               "components": [], "config": {"data": {"draft": None}}},
                    "schema": {"properties": {}}, "config_version": "revision"}
        before = json.dumps(original)
        view = ProjectConfiguration.view(original)
        self.assertEqual(view.project_id, "p")
        self.assertEqual(view.stored_config, {"data": {"saved": None}})
        self.assertEqual(view.values.config, {"data": {"draft": None}})
        self.assertIsNone(view.values.conversation_storage)
        view.values.config["data"]["draft"] = "changed"
        preview = ProjectConfiguration.with_values(original, view.values.to_dict())
        self.assertEqual(preview["project"]["config"], {"data": {"draft": "changed"}})
        self.assertEqual(preview["config_version"], "revision")
        self.assertEqual(json.dumps(original), before)
        del original["values"]["conversation_storage"]
        with self.assertRaises(KeyError):
            ProjectConfiguration.view(original)

    def test_builtin_reference_kinds_do_not_close_extension_kinds(self):
        reference = ResourceRef(ResourceKind.RUN, "run")
        self.assertEqual(reference.to_dict()["kind"], "run")
        self.assertIs(type(reference.kind), str)
        custom = ResourceRef("custom-component:entry", "entry", component="custom")
        self.assertEqual(ResourceRef.from_dict(custom.to_dict()), custom)

    def test_checkpoint_envelope_owns_shape_and_preserves_private_payload(self):
        original = {"node_type": "engine_record", "engine_scope": "parent/child", "checkpoint_name": "private",
                    "local_key": "step", "status": "started", "container": False, "requires_retry": True,
                    "payload": {"private": [1]}}
        encoded = json.dumps(original)
        envelope, payload = CheckpointEnvelope.read(original)
        self.assertEqual(envelope.local_key, "step")
        self.assertEqual(unwrap_record(original), ({"private": [1]}, "parent/child"))
        detached = CheckpointEnvelope.attach(envelope.to_dict(), payload)
        detached["payload"]["private"].append(2)
        self.assertEqual(json.dumps(original), encoded)
        for changes in ({"requires_retry": False}, {"container": None}, {"node_type": "private"}, {"payload": []}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    CheckpointEnvelope.read({**original, **changes})
        private = {"status": "custom", "data": [1]}
        self.assertEqual(unwrap_record(private), (private, None))

    def test_continuation_projections_preserve_originals_and_missing_evidence(self):
        decisions = {"node": {"approved": True}}
        value = ResumeRecord.create("source", "graph", "digest", ["node"], decisions)
        self.assertEqual(value, {"run_id": "source", "checkpoint": "graph", "digest": "digest",
                                 "retry_nodes": ["node"], "decisions": {"node": {"approved": True}}})
        decisions["node"]["approved"] = False
        self.assertTrue(value["decisions"]["node"]["approved"])
        child = ResumeRecord.for_child(value, decisions={"child": {"value": [1]}}, retry_nodes=[])
        self.assertEqual(child["digest"], "digest")
        self.assertEqual(child["retry_nodes"], [])
        self.assertEqual(value["retry_nodes"], ["node"])
        self.assertEqual(RestartRecord.create("source", "revision"), {"run_id": "source", "digest": "revision"})
        self.assertIsNone(ResumeRecord.source_id({}))
        self.assertEqual(ResumeRecord.source_id({"resume": value}), "source")
        with self.assertRaises(AttributeError):
            ResumeRecord.source_id({"resume": None})
        metadata = {"resume": value, "application": {"preserve": True}}
        ResumeRecord.remove(metadata)
        self.assertEqual(metadata, {"application": {"preserve": True}})

    def test_policy_fields_drive_schema_without_changing_null_or_missing(self):
        declared = describe_policies()["properties"]
        self.assertEqual(declared["run"], RunPolicy.schema())
        self.assertEqual(declared["context"], ContextPolicy.schema())
        self.assertEqual(set(declared["run"]["properties"]),
                         {"max_queued", "timeout_seconds", "max_capability_rounds"})
        self.assertEqual(normalize_policies({}), {})
        self.assertEqual(normalize_policies({"run": {"max_queued": None}}), {"run": {"max_queued": None}})
        self.assertIsNone(ContextPolicy().mode)
        with self.assertRaises(ValueError):
            normalize_policies({"context": {"mode": None}})
        for owner, section, key in ((RunPolicy, "run", "max_queued"),
                                     (ContextPolicy, "context", "max_turns")):
            for value in (False, True, -1, 0, 1.0, "1"):
                with self.subTest(owner=owner, value=value):
                    with self.assertRaises(ValueError):
                        owner(**{key: value})
                    with self.assertRaises(ValueError):
                        normalize_policies({section: {key: value}})

    async def test_project_description_is_detached_and_candidate_validation_does_not_save(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root) as app:
                project = await app.projects.acreate("title", config={"data": {"labels": ["stored"]}})
                before = (project.paths.root / "project.json").read_bytes()
                view = await project.adescribe_config()
                self.assertEqual(set(view["values"]), {"title", "conversation_storage", "components", "config"})
                self.assertEqual(set(view["schema"]["properties"]), set(view["values"]))
                view["values"]["config"]["data"]["labels"].append("edited")
                self.assertEqual(view["project"]["config"]["data"]["labels"], ["stored"])
                candidate = {"data": {"labels": ["candidate"]}}
                checked = await project.avalidate_config(candidate, expected_version=view["config_version"])
                checked["values"]["config"]["data"]["labels"].append("private")
                self.assertEqual(checked["project"]["config"]["data"]["labels"], ["candidate"])
                self.assertEqual(candidate, {"data": {"labels": ["candidate"]}})
                self.assertEqual((project.paths.root / "project.json").read_bytes(), before)
                self.assertEqual(checked["config_version"], view["config_version"])

    def test_storage_schema_and_owner_reject_the_same_invalid_values(self):
        schema = ConversationStorage.schema()
        self.assertEqual(json.loads(json.dumps(schema))["enum"], ["file", "memory", None])
        validator = Draft202012Validator(schema)
        for value in ("file", "memory", None):
            with self.subTest(value=value):
                ConversationStorage.validate(value)
                self.assertTrue(validator.is_valid(value))
        for value in (False, True, 0, 1, "", "sqlite", [], {}):
            with self.subTest(value=value):
                self.assertFalse(validator.is_valid(value))
                with self.assertRaises(ValueError):
                    ConversationStorage.validate(value)
                with self.assertRaises(ValueError):
                    Project("project", "title", ProjectPaths(Path("unused")), conversation_storage=value)

    def test_custom_storage_selection_is_not_replaced_with_file_default(self):
        factory = lambda session: session
        conversations = ProjectConversations(lambda session: None, default=factory)
        self.assertIsNone(conversations.resolve(None))
        sentinel = object()
        self.assertIs(conversations(sentinel), sentinel)
        for value in ("file", "memory"):
            with self.assertRaises(ValueError):
                conversations.resolve(value)
        with self.assertRaises(ValueError):
            ConversationStorage.validate(None, nullable=False)

    def test_schema_is_detached_from_later_validation(self):
        schema = ConversationStorage.schema()
        schema["enum"].append("sqlite")
        with self.assertRaises(ValueError):
            ConversationStorage.validate("sqlite")
        self.assertEqual(ConversationStorage.schema()["enum"], ["file", "memory", None])

    async def test_output_index_choice_reaches_the_actual_repository(self):
        for supplied in (None, 0, 7):
            with self.subTest(supplied=supplied), tempfile.TemporaryDirectory() as root:
                services = BackendServices() if supplied is None else BackendServices(output_index_stride=supplied)
                async with LargeLanguageModel(root, services=services) as app:
                    self.assertEqual(app.run_repository.output_journal.index_stride,
                                     128 if supplied is None else supplied)
        self.assertEqual(OutputJournal().index_stride, 128)
        self.assertEqual(RunRepository().output_journal.index_stride, 128)

    def test_invalid_index_choice_fails_at_the_owner(self):
        for value in (-1, True, 1.5, "128", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    OutputJournal(index_stride=value)
                with self.assertRaises(ValueError):
                    RunRepository(output_index_stride=value)
