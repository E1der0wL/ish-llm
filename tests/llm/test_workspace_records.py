"""Independent stored wire, corrupt evidence and actual interrupted-copy effects."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from llm.components.tools.builtin.workspace_records import (
    MissingWorkspaceRecord, WorkspaceOrigin, WorkspaceRecord,
)
from llm.components.tools.builtin.workspace_repository import WorkspaceRepository
from llm.components.tools.builtin.workspaces import TestWorkspaces
from llm.services.infrastructure.storage import atomic_json
from llm.services.runtime.processes import ProcessToolRunner


def stored():
    return {"storage_version": 1, "workspace_id": "a" * 32, "project_id": "project",
            "created_at": "2026-10-23T12:00:00+00:00",
            "created_by": {"session_id": "session", "run_id": "run", "step_id": "step"},
            "source_paths": ["selected.txt"], "ready": True}


def corruptions(value):
    for field in value:
        missing = deepcopy(value)
        del missing[field]
        yield "missing " + field, missing
    for field, invalid in (("storage_version", True), ("storage_version", 2),
                           ("workspace_id", "b" * 32), ("project_id", "foreign"),
                           ("created_at", None), ("created_by", []),
                           ("created_by", {"session_id": None, "run_id": None}),
                           ("created_by", {"session_id": 1, "run_id": None, "step_id": None}),
                           ("source_paths", "selected.txt"), ("source_paths", [None]),
                           ("ready", 1)):
        yield str((field, invalid)), {**deepcopy(value), field: invalid}
    yield "unknown field", {**deepcopy(value), "replay": True}


class WorkspaceRecordTests(unittest.TestCase):
    def test_writer_and_reader_share_exact_required_wire_and_detached_values(self):
        raw = stored()
        record = WorkspaceRecord.read(raw, project_id="project", workspace_id="a" * 32)
        self.assertEqual(record.to_dict(), raw)
        raw["source_paths"].append("unselected.txt")
        raw["created_by"]["run_id"] = "other"
        self.assertEqual(record.to_dict(), stored())
        pending = WorkspaceRecord(1, "a" * 32, "project", stored()["created_at"],
                                  WorkspaceOrigin("session", "run", "step"), ["selected.txt"])
        self.assertFalse(pending.ready)
        self.assertEqual(pending.published().to_dict(), stored())
        with self.assertRaisesRegex(ValueError, "incomplete"):
            pending.require_ready()
        pending.published().require_ready()

    def test_missing_corrupt_and_foreign_fields_never_become_valid_evidence(self):
        for label, value in corruptions(stored()):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "ownership record"):
                WorkspaceRecord.read(value, project_id="project", workspace_id="a" * 32)

    def test_missing_record_is_only_an_incomplete_observation(self):
        value = MissingWorkspaceRecord("a" * 32, "project")
        self.assertEqual(value.to_dict(), {"workspace_id": "a" * 32, "project_id": "project",
                                          "ready": False, "reason": "missing_record"})
        with self.assertRaisesRegex(ValueError, "incomplete"):
            value.require_ready()
        with self.assertRaises(ValueError):
            MissingWorkspaceRecord("a" * 32, "project", ready=True)
        with self.assertRaises(ValueError):
            MissingWorkspaceRecord("a" * 32, "project", reason="ready")

    def test_copy_and_publish_failure_keep_source_and_remove_partial_space(self):
        for failure in ("copy", "publish"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "source"
                source.mkdir()
                original = source / "selected.txt"
                original.write_bytes(b"original\x00data")
                original.chmod(0o750)
                before = (original.read_bytes(), original.stat().st_mode)
                repo = WorkspaceRepository(root / "spaces", source)
                owner = SimpleNamespace(project_id="project", session_id="session", run_id="run", step_id="step")
                writes = []

                def publish(path, record):
                    writes.append(deepcopy(record))
                    if failure == "publish" and record["ready"]:
                        self.assertEqual((path.parent / "files/selected.txt").read_bytes(), before[0])
                        raise OSError("injected publication interruption")
                    atomic_json(path, record)

                def copy(parent_fd, name, target):
                    target.write_bytes(b"partial")
                    raise OSError("injected copy interruption")

                with patch("llm.components.tools.builtin.workspace_repository.atomic_json", side_effect=publish):
                    if failure == "copy":
                        with patch.object(repo, "_copy", side_effect=copy), self.assertRaises(OSError):
                            repo.create(owner, ["selected.txt"])
                    else:
                        with self.assertRaises(OSError):
                            repo.create(owner, ["selected.txt"])
                self.assertEqual([item["ready"] for item in writes], [False, True] if failure == "publish" else [False])
                self.assertEqual((original.read_bytes(), original.stat().st_mode), before)
                self.assertEqual(repo.list("project").to_dict(), {"workspaces": []})
                self.assertEqual({path.name for path in (repo.root / "project").iterdir()}, {".locks"})


class WorkspaceCorruptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_corruption_blocks_execution_reads_and_delete_without_rewriting_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            original = source / "selected.txt"
            original.write_bytes(b"original")
            spaces = TestWorkspaces(root / "spaces", source_root=source, allow_network=False)
            self.addAsyncCleanup(spaces.close)
            owner = SimpleNamespace(project_id="project", session_id="session", run_id="run", step_id="step")
            with patch("llm.components.tools.builtin.workspaces.current_tool_call", return_value=owner):
                record = await spaces.create({"source_paths": ["selected.txt"]})
                identifier = record["workspace_id"]
                directory = root / "spaces/project" / identifier
                evidence = directory / "record.json"
                for label, value in corruptions(record):
                    with self.subTest(label=label):
                        payload = json.dumps(value).encode()
                        evidence.write_bytes(payload)
                        with patch.object(ProcessToolRunner, "execute") as execute:
                            with self.assertRaises(ValueError):
                                await spaces({"workspace_id": identifier, "argv": ["/bin/touch", "effect"]})
                            execute.assert_not_called()
                        with self.assertRaises(ValueError):
                            await spaces.file_call("file_read", {"workspace_id": identifier, "path": "selected.txt"})
                        with self.assertRaises(ValueError):
                            await spaces.delete({"workspace_id": identifier})
                        self.assertEqual(evidence.read_bytes(), payload)
                        self.assertFalse((directory / "files/effect").exists())
                        self.assertEqual((directory / "files/selected.txt").read_bytes(), b"original")
                        self.assertEqual(original.read_bytes(), b"original")
