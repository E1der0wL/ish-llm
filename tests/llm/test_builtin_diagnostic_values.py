"""Independent diagnostic wire expectations and observation failure boundaries."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from llm.components.tools.builtin.diagnostics import DiagnosticTools
from llm.components.tools.builtin.diagnostic_values import (ProcessListArguments, ProcessInspectArguments,
    ProcessDetails, ProcessInspection, FileDescriptorTarget, UnavailableFileDescriptor)
from llm.core.configuration import UNSET
from llm.services.infrastructure.process_state import ProcessStat


class DiagnosticValuesTests(unittest.TestCase):
    def observation(self, **changes):
        return replace(ProcessStat(7, "sample", "S", 1, 7, 7, 0, -1, 29), **changes)

    def test_declaration_drives_schema_without_materializing_request_defaults(self):
        raw = {"pid": 7}
        original = deepcopy(raw)
        selected = ProcessInspectArguments.read(raw)
        self.assertEqual(raw, original)
        self.assertIs(selected.expected_start_ticks, UNSET)
        self.assertFalse(selected.include_files)
        self.assertIsNone(ProcessInspectArguments.read({"pid": 7, "expected_start_ticks": None}).expected_start_ticks)
        self.assertEqual(ProcessInspectArguments.schema(), {"type": "object", "additionalProperties": False,
            "properties": {"pid": {"type": "integer", "minimum": 1},
                           "expected_start_ticks": {"type": "integer", "minimum": 0},
                           "include_files": {"type": "boolean"}}, "required": ["pid"]})
        schema = ProcessListArguments.schema()
        schema["properties"]["query"]["type"] = "integer"
        self.assertEqual(ProcessListArguments.schema()["properties"]["query"], {"type": "string"})
        with self.assertRaises(KeyError):
            ProcessInspectArguments.read({})

    def test_missing_observations_stay_omitted_with_explicit_unavailability(self):
        value = ProcessInspection(self.observation())
        value.observe(ProcessDetails.cwd, lambda: "/work")
        value.observe(ProcessDetails.executable, lambda: (_ for _ in ()).throw(PermissionError()))
        value.observe(ProcessDetails.argv, lambda: [])
        value.observe(ProcessDetails.files, lambda: [FileDescriptorTarget(1, "pipe:[1]"),
            UnavailableFileDescriptor(2, "FileNotFoundError")])
        self.assertEqual(value.to_dict(observed_at="now"), {"pid": 7, "name": "sample", "state": "S", "ppid": 1,
            "process_group": 7, "session": 7, "tty_number": 0, "foreground_process_group": -1,
            "start_ticks": 29, "cwd": "/work", "argv": [],
            "files": [{"fd": 1, "target": "pipe:[1]"}, {"fd": 2, "unavailable": "FileNotFoundError"}],
            "observed_at": "now", "unavailable": {"executable": "PermissionError"}})
        with self.assertRaises(ValueError):
            value.observe(ProcessDetails.stdin, lambda: (_ for _ in ()).throw(ValueError("malformed")))

    def test_process_paging_and_failed_observations_preserve_wire_shape(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("7", "8", "9", "self"):
                (root / name).mkdir()
            tool = DiagnosticTools(root)
            with patch.object(ProcessStat, "ROOT", root), patch.object(tool, "_stat", side_effect=(
                    PermissionError(), self.observation(pid=8), self.observation(pid=9))):
                page = tool.processes({"limit": 1})
            self.assertEqual(page, {"processes": [{"pid": 8, "name": "sample", "state": "S", "ppid": 1,
                "process_group": 7, "session": 7, "tty_number": 0, "foreground_process_group": -1,
                "start_ticks": 29}], "unavailable": [{"pid": 7, "reason": "PermissionError"}], "next_pid": 8})
            with patch.object(ProcessStat, "ROOT", root), patch.object(tool, "_stat", return_value=self.observation(pid=9)):
                page = tool.processes({"after_pid": 8, "query": "SAM"})
            self.assertEqual([row["pid"] for row in page["processes"]], [9])
            self.assertIsNone(page["next_pid"])

    def test_pid_change_and_explicit_null_never_merge_different_observations(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "7").mkdir()
            (root / "7" / "cmdline").write_bytes(b"sample\0\xff\0")
            tool = DiagnosticTools(root)
            with patch.object(ProcessStat, "ROOT", root), patch.object(tool, "_stat", side_effect=(
                    self.observation(), self.observation(start_ticks=30))):
                with self.assertRaisesRegex(ValueError, "changed during"):
                    tool.inspect({"pid": 7})
            with patch.object(tool, "_stat", return_value=self.observation()):
                with self.assertRaisesRegex(ValueError, "changed; list"):
                    tool.inspect({"pid": 7, "expected_start_ticks": None})
            with patch.object(ProcessStat, "ROOT", root), patch.object(tool, "_stat", return_value=self.observation()):
                result = tool.inspect({"pid": 7})
            self.assertEqual(result["argv"], ["sample", "\ufffd"])
            self.assertNotIn("cwd", result)
            self.assertEqual(result["unavailable"]["cwd"], "FileNotFoundError")
            self.assertNotIn("files", result)
