"""Independent storage and admission evidence for typed checkpoint records."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from llm.core.checkpoint_records import (CheckpointManifest, CheckpointSnapshot, CheckpointRecord,
    CheckpointDeclarations, CheckpointDeletion, CheckpointWrite)
from llm.core.interactions import InteractionBinding, InteractionSelection, approval_request


class CheckpointValueTests(unittest.TestCase):
    def manifest(self):
        return {"schema_version": 1, "run_id": "run", "engine": "loop",
            "header": {"engine_private": {"mode": "explicit"}},
            "records": {"step": {"status": "completed", "effect": {"count": 1}}},
            "record_revisions": {}}

    def test_manifest_and_public_snapshot_keep_exact_existing_wire(self):
        raw = self.manifest()
        manifest = CheckpointManifest.from_dict(raw)
        manifest.require_owner(SimpleNamespace(id="run", engine="loop"))
        self.assertEqual(manifest.to_dict(), raw)
        snapshot = manifest.resolved(manifest.records)
        self.assertEqual(snapshot.to_dict(), {key: value for key, value in raw.items() if key != "record_revisions"})
        self.assertEqual(CheckpointSnapshot.from_dict(snapshot.to_dict()), snapshot)
        with self.assertRaises(ValueError):
            manifest.require_owner(SimpleNamespace(id="other", engine="loop"))

    def test_missing_fields_and_non_integer_version_never_rebuild_storage(self):
        original = self.manifest()
        for name in original:
            broken = deepcopy(original)
            del broken[name]
            before = deepcopy(broken)
            with self.subTest(field=name), self.assertRaises((ValueError, TypeError)):
                CheckpointManifest.from_dict(broken)
            self.assertEqual(broken, before)
        for version in (True, 1.0, "1", None, 0, 2):
            with self.subTest(version=version), self.assertRaises(ValueError):
                CheckpointManifest.from_dict({**original, "schema_version": version})

    def test_revision_update_and_serialization_do_not_change_original_payload(self):
        raw = self.manifest()
        manifest = CheckpointManifest.from_dict(raw)
        changed = manifest.with_revision("step", "a" * 64)
        raw["header"]["engine_private"]["mode"] = "changed"
        emitted = changed.to_dict()
        emitted["records"]["step"]["effect"]["count"] = 99
        self.assertEqual(manifest.record_revisions, {})
        self.assertEqual(changed.record_revisions, {"step": "a" * 64})
        self.assertEqual(changed.header, {"engine_private": {"mode": "explicit"}})
        self.assertEqual(changed.records["step"]["effect"], {"count": 1})
        with self.assertRaises(ValueError):
            manifest.with_revision("step", "unknown")

    def test_declarations_require_both_original_witnesses(self):
        for metadata in ({"checkpoints": []}, {"checkpoint_revisions": {}},
                         {"checkpoints": ["loop"], "checkpoint_revisions": {}},
                         {"checkpoints": ["loop"], "checkpoint_revisions": {"loop": "invalid"}}):
            original = deepcopy(metadata)
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                CheckpointDeclarations.read(metadata)
            self.assertEqual(metadata, original)
        self.assertEqual(CheckpointDeclarations.read({}).names, ())

    def test_deleted_checkpoint_retains_approval_wire_and_rejects_wrong_binding(self):
        request = approval_request("Approve").bind("loop", "step")
        raw = {"deleted_at": "2026-10-23T00:00:00+00:00", "revision": "reviewed",
            "names": ["loop"], "interactions": [request.to_dict()]}
        deleted = CheckpointDeletion.from_dict(raw)
        self.assertEqual(deleted.to_dict(), raw)
        self.assertEqual(deleted.interactions[0].fingerprint, request.fingerprint)
        for bad in ({**raw, "names": ["other"]}, {**raw, "interactions": [request.to_dict()] * 2}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                CheckpointDeletion.from_dict(bad)

    def test_binding_preserves_absence_selection_identity_and_opaque_data(self):
        binding = InteractionBinding.for_checkpoint("loop", "step", input_key=None, selection=False)
        self.assertEqual(binding.to_dict(), {"checkpoint": "loop", "key": "step", "input_key": None})
        retry = InteractionBinding.for_retry("loop", "step", "retry_nodes")
        self.assertEqual(retry.to_dict(), {"checkpoint": "loop", "key": "step", "target": "retry_nodes"})
        raw = {"checkpoint": "loop", "selection": 1, "extension": {"revision": 7}}
        value = InteractionBinding(raw)
        raw["extension"]["revision"] = 99
        self.assertFalse(value.selection)
        self.assertEqual(value.to_dict()["extension"], {"revision": 7})
        with self.assertRaises(KeyError):
            value.required_key
        with self.assertRaises(ValueError):
            InteractionSelection.from_dict({"option_id": "approve"})

    def test_only_initialization_may_supply_an_empty_record_map(self):
        value = CheckpointWrite({"name": "loop", "operation": "initialize", "header": {}})
        self.assertEqual(value.interactions().records, {})
        self.assertEqual(value.initial_manifest(SimpleNamespace(id="run", engine="loop")).records, {})
        with self.assertRaises(KeyError):
            CheckpointWrite({"name": "loop", "operation": "record", "key": "step"}).record
        with self.assertRaises(ValueError):
            CheckpointRecord.from_dict({"key": "step"})

    def test_event_factories_preserve_omission_and_detach_effect_evidence(self):
        self.assertEqual(CheckpointWrite.initialize_event("loop", {}).to_dict(),
                         {"name": "loop", "operation": "initialize", "header": {}})
        self.assertEqual(CheckpointWrite.initialize_event("loop", {}, {}).to_dict(),
                         {"name": "loop", "operation": "initialize", "header": {}, "records": {}})
        original = {"name": "graph", "operation": "record", "key": "node",
                    "value": {"effect": {"count": 1}}, "lifecycle": "complete"}
        write = CheckpointWrite(original)
        value = write.record.value
        value["usage"] = {"calls": 2}
        changed = write.with_record(value).to_dict()
        self.assertEqual(changed, {**original, "value": {"effect": {"count": 1}, "usage": {"calls": 2}}})
        self.assertEqual(write.to_dict(), original)
        self.assertEqual(CheckpointWrite.record_event("graph", "node", original["value"]).to_dict(),
                         {key: value for key, value in original.items() if key != "lifecycle"})


if __name__ == "__main__":
    unittest.main()
