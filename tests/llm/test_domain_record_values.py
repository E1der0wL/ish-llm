"""Independent current-record and owned-path expectations survive ownership moves."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from llm.core.models import Project, Run, RunStatus, validate_domain_version
from llm.core.paths import ProjectPaths, SessionPaths, RunPaths, StepPaths


class DomainRecordValueTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"id": "project", "title": "Title", "config": {
            "policies": {}, "parameters": {}, "data": {"opaque": [1.0]}},
            "created_at": "2026-10-23T00:00:00+00:00", "deleted": False,
            "components": ["active"], "initialized_components": ["active", "disabled"],
            "conversation_storage": "file", "storage_version": 1}

    def test_project_writer_preserves_wire_shape_and_detaches_configuration(self):
        before = deepcopy(self.raw)
        value = Project.from_record(self.raw, ProjectPaths(Path("workspace/project")))
        encoded = value.to_record()
        self.assertEqual(encoded, before)
        self.assertEqual(set(encoded), set(before))
        self.assertNotIn("paths", encoded)
        encoded["config"]["data"]["opaque"].clear()
        encoded["components"].clear()
        self.assertEqual(value.to_record(), before)
        self.assertEqual(self.raw, before)

    def test_project_writer_cannot_serialize_invalid_component_lifetime(self):
        value = Project.from_record(self.raw, ProjectPaths(Path("workspace/project")))
        value.components = ("missing",)
        with self.assertRaisesRegex(ValueError, "initialized storage"):
            value.to_record()
        value.components = "active"
        with self.assertRaisesRegex(ValueError, "sequence"):
            value.to_record()

    def test_version_guard_rejects_coercions_without_mutating_input(self):
        for version in (True, 1.0, "1", None, 0, 2):
            damaged = {**self.raw, "storage_version": version}
            before = deepcopy(damaged)
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_domain_version(damaged)
            self.assertEqual(damaged, before)
        with self.assertRaises(ValueError):
            validate_domain_version({key: value for key, value in self.raw.items() if key != "storage_version"})

    def test_index_is_an_immutable_projection_and_not_a_complete_run_record(self):
        raw = {"id": "run", "session_id": "session", "status": "pending",
               "started_at": None, "ended_at": None}
        value = Run.index_record(raw)
        self.assertEqual((value.id, value.session_id, value.status, value.started_at, value.ended_at),
                         ("run", "session", RunStatus.PENDING, None, None))
        raw["id"] = "modified"
        self.assertEqual(value.id, "run")
        with self.assertRaises(FrozenInstanceError):
            value.status = RunStatus.RUNNING
        with self.assertRaises(ValueError):
            Run.from_record(raw, RunPaths(Path("unused")))

    def test_paths_keep_existing_storage_layout_and_structural_output_contract(self):
        root = Path("workspace/owned")
        for owner, expected in (
            (ProjectPaths, {"record": "project.json", "agents": "agents", "sessions": "sessions", "state": "state", "logs": "logs", "cache": "cache"}),
            (SessionPaths, {"record": "session.json", "conversation": "conversation.jsonl", "runs": "runs", "attachments": "attachments", "state": "state", "logs": "logs"}),
            (RunPaths, {"record": "run.json", "outputs": "state/outputs.jsonl", "steps": "steps", "state": "state", "logs": "logs"}),
            (StepPaths, {"record": "step.json", "state": "state", "logs": "logs", "artifacts": "artifacts"}),
        ):
            value = owner(root)
            for name, relative in expected.items():
                with self.subTest(owner=owner, field=name):
                    self.assertEqual(getattr(value, name), root / relative)
        self.assertEqual(RunPaths.output_path(root), root / "outputs.jsonl")
