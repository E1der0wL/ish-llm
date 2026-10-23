"""Independent registration, execution configuration and binding expectations."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components.tools.builtin import BuiltinTools, TestWorkspaces
from llm.components.tools.builtin.workspace_values import WorkspaceExecuteArguments, WorkspaceRuntime
from llm.core.configuration import UNSET
from llm.services.infrastructure.process_result import ProcessResult
from llm.services.infrastructure.process_command import ProcessCommand
from hub.backend.tools.test_workspaces import HubTestWorkspaces


class BuiltinCatalogExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_git_registration_cannot_replace_configured_shell_command(self):
        with tempfile.TemporaryDirectory() as root:
            async with BuiltinTools(root, allow_commands=True, shell=["/bin/sh", "-c"], git=True) as toolkit:
                tool, arguments = toolkit.registry.prepare("shell_execute", {"command": "printf configured-shell"})
                result = await tool.handler(arguments)
                self.assertEqual(result["returncode"], 0, result["stderr"])
                self.assertEqual(result["stdout"], "configured-shell")

    async def test_host_adapter_gets_opaque_arguments_and_contract_without_defaults(self):
        received = []

        async def adapter(arguments):
            received.append(deepcopy(arguments))
            return {"host_result": arguments}

        with tempfile.TemporaryDirectory() as root:
            async with BuiltinTools(root, adapters={"external_rag_search": adapter, "browser_act": adapter}) as toolkit:
                raw = {"corpus": "remote", "query": "question", "options": {"extension": {"knob": None}}}
                tool, args = toolkit.registry.prepare("external_rag_search", raw)
                self.assertFalse(tool.contract.approval_required)
                self.assertEqual(tool.contract.effect, "unknown")
                self.assertEqual(await tool.handler(args), {"host_result": raw})
                self.assertEqual(received, [raw])
                self.assertTrue(toolkit.registry.get("browser_act").contract.approval_required)

    async def test_workspace_binding_is_detached_and_missing_environment_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            source.mkdir()
            spaces = TestWorkspaces(Path(root) / "spaces", source_root=source, allow_network=False)
            self.addAsyncCleanup(spaces.close)
            original = spaces.execution_binding
            self.assertEqual(original, {"root": str(Path(root) / "spaces"), "source_root": str(source),
                "isolation": "sandbox", "allow_network": False, "read_only_paths": [], "env": None,
                "max_output_bytes": None, "memory_bytes": None, "python_executable": spaces._runtime.python_executable})
            original["read_only_paths"].append("/")
            original["allow_network"] = True
            self.assertFalse(spaces.execution_binding["allow_network"])
            self.assertEqual(spaces.execution_binding["read_only_paths"], [])
            async with BuiltinTools(source, test_workspaces=spaces) as toolkit:
                original = toolkit._execution_binding.to_dict()
                self.assertEqual(original["test_workspaces"], spaces.execution_binding)
                original["test_workspaces"]["allow_network"] = True
                self.assertFalse(toolkit._execution_binding.test_workspaces.allow_network)

    async def test_invalid_hub_command_has_no_execution_and_retains_feedback_wire(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            source.mkdir()
            spaces = HubTestWorkspaces(Path(root) / "spaces", source_root=source, allow_network=False)
            self.addAsyncCleanup(spaces.close)
            with patch.object(TestWorkspaces, "__call__") as execute:
                for argv in (None, [], ["relative"], [""], ["/bin/sh", None], ["/bin/sh", "\0"]):
                    with self.subTest(argv=argv):
                        raw = {"argv": argv, "_hub_risk": {"level": "low", "reason": "test"}}
                        before = deepcopy(raw)
                        result = await spaces(raw)
                        self.assertEqual(result, {"status": "invalid_arguments", "executed": False,
                            "error": "argv must be a nonempty list of strings with an absolute executable at argv[0].",
                            "guidance": "Correct the arguments and call test_workspace_execute again. "
                                        "For a shell command use ['/bin/sh', '-c', 'your command']. "
                                        "For Python use the actual absolute Python executable. No command was executed."})
                        self.assertEqual(raw, before)
                execute.assert_not_called()


class BuiltinCatalogValueTests(unittest.TestCase):
    def test_workspace_schema_presence_and_environment_replacement(self):
        raw = {"workspace_id": "a" * 32, "argv": ["/bin/true"]}
        value = WorkspaceExecuteArguments.read(raw)
        self.assertIs(value.env, UNSET)
        self.assertNotIn("env", raw)
        runtime = WorkspaceRuntime(allow_network=False, env={"SELECTED": "host"})
        self.assertIs(runtime.with_environment(value.env), runtime)
        self.assertEqual(runtime.with_environment({}).env, {})
        self.assertEqual(runtime.env, {"SELECTED": "host"})
        for invalid in (None, [], {"bad=key": "value"}, {"x": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                runtime.with_environment(invalid)
        schema = WorkspaceExecuteArguments.schema()
        self.assertEqual(schema["required"], ["workspace_id", "argv"])
        self.assertEqual(schema["properties"]["workspace_id"], {"type": "string", "pattern": "^[0-9a-f]{32}$"})
        self.assertEqual(schema["properties"]["argv"], {"type": "array", "minItems": 1, "items": {"type": "string"}})
        schema["properties"].clear()
        self.assertEqual(set(WorkspaceExecuteArguments.schema()["properties"]), {"workspace_id", "argv", "env"})

    def test_command_and_result_owners_preserve_independent_wire(self):
        self.assertEqual(ProcessResult.decoded(-9, b"out\xff", b"err").to_dict(),
                         {"returncode": -9, "stdout": "out\ufffd", "stderr": "err"})
        for value, valid in ((["/bin/sh", ""], True), (("/bin/sh", "-c", "true"), True),
                             (["sh"], False), ([""], False), ([], False), (None, False),
                             (["/bin/sh", "\0"], False), ([1], False)):
            with self.subTest(value=value):
                self.assertEqual(ProcessCommand.valid(value), valid)
                if not valid:
                    with self.assertRaisesRegex(ValueError, "absolute executable"):
                        ProcessCommand.validate(value)
