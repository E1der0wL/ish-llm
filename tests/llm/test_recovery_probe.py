"""배포 폴더만으로 실행되는 장애 검사 CLI의 실제 자식 프로세스와 보고서를 검증한다."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from examples.llm.recovery_probe import check, child, ready
from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.infrastructure.storage import atomic_json


class RecoveryProbeTests(unittest.TestCase):
    def test_all_cases_use_real_processes_and_preserve_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, "-m", "examples.llm.recovery_probe",
                "--output-dir", root, "--timeout", "45", "--processes", "2"],
                capture_output=True, text=True, timeout=180)
            reports = list(Path(root).glob("*/report.json"))
            self.assertEqual(len(reports), 1, result.stderr)
            report = json.loads(reports[0].read_text())
            diagnostics = "\n".join(f"{path}:\n{path.read_text(errors='replace')}"
                for path in reports[0].parent.rglob("*.log") if "workspace" not in path.parts) if result.returncode else ""
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr + diagnostics)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(set(report["cases"]), {"crash", "disk-full", "connection", "multiprocess"})
            self.assertEqual(report["cases"]["crash"]["details"]["signal"], "SIGKILL")
            self.assertTrue(report["cases"]["crash"]["details"]["recovery"]["no_replay"])
            disk = report["cases"]["disk-full"]["details"]["stages"]
            self.assertEqual({key: value["recovery"]["first_run_status"] for key, value in disk.items()},
                             {"begin": "completed", "output": "failed", "finish": "interrupted"})
            self.assertTrue(all(value["failure"]["injected_errno"] == 28 for value in disk.values()))
            connection = report["cases"]["connection"]["details"]
            self.assertEqual(connection["requests"], ["warmup", "disconnect-before", "continue", "disconnect-mid", "continue"])
            self.assertEqual([value["partial_text"] for value in connection["observations"]], ["", "partial"])
            competitors = report["cases"]["multiprocess"]["details"]
            self.assertEqual(len(competitors["contenders"]), 2)
            self.assertTrue(competitors["records_unchanged"])

    def test_invalid_arguments_do_not_create_workspaces(self):
        with tempfile.TemporaryDirectory() as root:
            for option, value in (("--timeout", "nan"), ("--timeout", "0"), ("--processes", "1")):
                result = subprocess.run([sys.executable, "-m", "examples.llm.recovery_probe",
                    "--output-dir", root, option, value], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_checks_are_not_python_assert_statements(self):
        with self.assertRaisesRegex(AssertionError, "expected"):
            check(False, "expected")

    def test_controller_exception_kills_and_reaps_owned_holder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root / "probe.json", {"case": "crash"})
            with self.assertRaisesRegex(RuntimeError, "controller stopped"):
                with child(root, "hold", 30) as process:
                    ready(process, root, 20)
                    raise RuntimeError("controller stopped")
            self.assertEqual(process.poll(), -9)
            with WorkspaceOwnership(root / "workspace" / "projects").scope():
                pass
