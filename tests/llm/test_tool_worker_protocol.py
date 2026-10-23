"""Independent IPC wire and bootstrap assertions; real effects remain in worker tests."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from llm.core.tool_worker import (ToolWorkerRequest, ToolWorkerResponse, ToolWorkerDescriptor,
    ToolWorkerFailure, ToolWorkerFailureKind, ToolWorkerProtocolError)
from llm.components.tools import Tool, ToolContract, ToolEffect, ToolIsolation
from llm.core.model_protocol import ToolDefinition
from llm.components.tools import process
from llm.services.runtime._worker import ProcessLaunch
from llm.services.runtime.tools import ToolExecutionError


class ToolWorkerProtocolTests(unittest.TestCase):
    def test_request_and_launch_wire_preserve_required_fields_and_detach(self):
        wire = {"operation": "inspect", "tool_name": "test", "project_id": "project",
                "source_path": "/tools/test/main.py", "source_hash": "source",
                "requirements_hash": "requirements", "plugin_lib": None}
        request = ToolWorkerRequest.read(wire)
        self.assertEqual(request.to_dict(), wire)
        arguments = {"nested": [1]}
        call = {"source": ["run"]}
        execution = request.execute("descriptor", arguments, call)
        arguments["nested"].append(2)
        call["source"].append("changed")
        expected = {**wire, "operation": "execute", "descriptor_hash": "descriptor",
                    "arguments": {"nested": [1]}, "call": {"source": ["run"]}}
        self.assertEqual(execution.to_dict(), expected)
        self.assertEqual(ToolWorkerRequest.read(expected).to_dict(), expected)
        self.assertEqual(request.binding().to_dict(),
                         {"source_hash": "source", "requirements_hash": "requirements"})
        for missing in expected:
            candidate = {k: v for k, v in expected.items() if k != missing}
            with self.subTest(missing=missing), self.assertRaises((KeyError, TypeError, ValueError)):
                ToolWorkerRequest.read(candidate)
        launch = {"argv": ["/bin/true"], "cwd": "/tmp", "env": {},
                  "payload": None, "memory_bytes": None}
        selected = ProcessLaunch.read(launch)
        self.assertEqual(selected.to_dict(), launch)
        launch["argv"].append("changed")
        launch["env"]["INJECTED"] = "changed"
        self.assertEqual(selected.argv, ["/bin/true"])
        self.assertEqual(selected.env, {})
        for missing in launch:
            with self.subTest(launch_missing=missing), self.assertRaises(TypeError):
                ProcessLaunch.read({k: v for k, v in launch.items() if k != missing})

    def test_descriptor_wire_and_fingerprint_are_unchanged(self):
        contract = ToolContract("1", ToolEffect.UNKNOWN, ToolIsolation.NONE, False, False, ())
        definition = ToolDefinition("test", "A test", {"type": "object"})
        tool = Tool("test", "A test", {"type": "object"}, None, definition, contract=contract)
        expected = {"name": "test", "description": "A test", "parameters": {"type": "object"},
                    "definition": {"name": "test", "description": "A test", "parameters": {"type": "object"},
                                   "strict": None, "opaque": {}},
                    "contract": {"revision": "1", "effect": "unknown", "isolation": "none",
                                 "approval_required": False, "operation_key_required": False, "retry_fields": []}}
        value = ToolWorkerDescriptor.from_tool(tool)
        self.assertEqual(value.to_dict(), expected)
        self.assertEqual(ToolWorkerDescriptor.read(expected, name="test").to_dict(), expected)
        self.assertEqual(process.descriptor_fingerprint(value.to_dict()), process.descriptor_fingerprint(expected))
        for missing in expected:
            with self.subTest(missing=missing), self.assertRaises(ToolWorkerProtocolError):
                ToolWorkerDescriptor.read({k: v for k, v in expected.items() if k != missing}, name="test")
        with self.assertRaises(ToolWorkerProtocolError):
            ToolWorkerDescriptor.read(expected, name="other")

    def test_error_reader_never_restores_missing_effect_classification(self):
        failure = {"kind": "tool_execution", "message": "failed", "code": "tool_failed",
                   "effect": "none", "retryable": False, "recoverable": True}
        wire = {"ok": False, "error": failure}
        self.assertEqual(ToolWorkerResponse.read(wire).to_dict(), wire)
        self.assertEqual(ToolWorkerResponse.success(None).to_dict(), {"ok": True, "value": None})
        self.assertEqual(ToolWorkerResponse.failure(ToolWorkerFailure(
            ToolWorkerFailureKind.UNKNOWN, "uncertain")).to_dict(),
            {"ok": False, "error": {"kind": "unknown", "message": "uncertain"}})
        with self.assertRaises(ToolExecutionError) as caught:
            process._decode(wire, "execute")
        self.assertEqual(caught.exception.effect, "none")
        self.assertIs(caught.exception.recoverable, True)
        for field in ("kind", "code", "message", "effect", "retryable", "recoverable"):
            with self.subTest(missing=field), self.assertRaises(process.WorkerError):
                process._decode({"ok": False, "error": {k: v for k, v in failure.items() if k != field}}, "execute")
        with self.assertRaises(process.WorkerError):
            process._decode({"ok": False, "error": {**failure, "effect": "uncertain"}}, "execute")
        # Existing failure-envelope extensions remain observational, never authority.
        with self.assertRaises(ToolExecutionError):
            process._decode({**wire, "extra": True, "error": {**failure, "extra": True}}, "execute")
        with self.assertRaises(process.WorkerError):
            process._decode({"ok": True, "value": 1, "extra": True}, "execute")

    def test_bootstrap_protocol_import_needs_no_site_or_component_imports(self):
        root = str(Path(__file__).resolve().parents[2])
        script = ("import json,sys;sys.path.insert(0,sys.argv[1]);"
                  "from llm.core.tool_worker import ToolWorkerRequest;"
                  "print(json.dumps([n for n in sys.modules if n.startswith(('llm.components', 'llm.providers', 'jsonschema'))]))")
        result = subprocess.run([sys.executable, "-I", "-S", "-c", script, root],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), [])
