"""Independent sandbox command wire and isolated startup transport checks."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.services.infrastructure.linux_sandbox import LinuxSandbox
from llm.services.runtime._worker import ProcessGate, ProcessLaunch


class SandboxContractTests(unittest.TestCase):
    def test_command_keeps_target_environment_after_clearenv_and_rejects_host_mounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            cwd = Path(temporary)
            env = {"LD_PRELOAD": "selected-target-value", "X": "한글 space=$HOME"}
            command = ["/bin/sh", "-c", "printf selected"]
            options = dict(cwd=cwd, python="/usr/bin/python3", read_only_paths=(), env=env,
                           allow_network=False, trusted_runner=Path("/trusted/runner.py"))
            with patch("llm.services.infrastructure.linux_sandbox.shutil.which", return_value="/usr/bin/bwrap"):
                args = LinuxSandbox.command(command, **options)
                self.assertEqual(args[:14], ["/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--die-with-parent",
                    "--new-session", "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--ro-bind"])
                self.assertNotIn("--share-net", args)
                index = args.index("--clearenv")
                self.assertEqual(args[index:], ["--clearenv", "--setenv", "LD_PRELOAD", "selected-target-value",
                    "--setenv", "X", "한글 space=$HOME", "--bind", str(cwd), str(cwd), "--chdir", str(cwd),
                    "--", "/bin/sh", "-c", "printf selected"])
                self.assertEqual(command, ["/bin/sh", "-c", "printf selected"])
                for path in ("/", "/proc", "/proc/self", "/dev", "/sys"):
                    with self.subTest(mount=path), self.assertRaisesRegex(ValueError, "host or special"):
                        LinuxSandbox.command(command, **{**options, "read_only_paths": (Path(path),)})
                with self.assertRaisesRegex(ValueError, "dedicated directory"):
                    LinuxSandbox.command(command, **{**options, "cwd": Path("/etc")})
                with self.assertRaisesRegex(ValueError, "trusted runner"):
                    LinuxSandbox.command(command, **{**options, "cwd": Path("/trusted")})

    def test_launch_encoding_preserves_exact_selected_fields_and_input(self):
        raw = {"argv": ["/bin/true"], "cwd": "/selected", "env": {"X": "한글"},
               "payload": {"extension": [False, None]}, "memory_bytes": None}
        before = deepcopy(raw)
        value = ProcessLaunch.read(raw)
        encoded = value.encode()
        self.assertIn("한글".encode("utf-8"), encoded)
        self.assertEqual(json.loads(encoded), before)
        raw["env"]["X"] = "changed"
        self.assertEqual(json.loads(value.encode()), before)
        with self.assertRaises(ValueError):
            ProcessLaunch.read({**before, "payload": float("nan")}).encode()

    def test_startup_gate_environment_is_detached_from_target_and_other_calls(self):
        selected = ProcessGate.environment()
        self.assertEqual(selected, {})
        selected["LD_PRELOAD"] = "must not inherit"
        self.assertEqual(ProcessGate.environment(), {})
