"""ish execution snapshots survive queueing, approval and concurrent sessions."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from hub.config.environment import ENVIRONMENT_OPTION, ShellEnvironment
from hub.backend.execution.environment import EnvironmentEngine
from hub.backend.runtime import HubConfig, HubRuntime
from hub.ui.live import LiveController, LiveHubView
from hub.model import HubSnapshot, SessionSummary
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm.support.runtime_tools import TEST_CONTRACT


def snapshot(root, marker):
    return ShellEnvironment.from_dict({"cwd": str(root), "env": {
        "PWD": str(root), "PATH": "/usr/bin:/bin", "HUB_SHELL_TEST": marker}})


class InvestigationGuidanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_project_receives_direct_investigation_and_search_order(self):
        from hub.backend.execution.environment import EnvironmentSession
        from hub.asset.guides import WORK_GUIDANCE
        request = NS(messages=[])
        async for _ in EnvironmentSession("/work/project").prepare(request):
            pass
        text = request.messages[0].value["content"]
        self.assertIn("/work/project", text)
        self.assertIn(WORK_GUIDANCE, text)
        self.assertIn("Do not ask the user to run --help", text)
        self.assertLess(text.index("registered RAG documents"), text.index("then existing model knowledge"))
        self.assertIn("does not currently provide Internet search", text)


class ShellSnapshotTests(unittest.TestCase):
    def test_copies_host_values_and_rejects_unavailable_or_invalid_context(self):
        env = {"PWD": "/tmp", "EXAMPLE": "first"}
        prompt = NS(context=NS(environ=env))
        captured = ShellEnvironment.from_prompt(prompt)
        env["EXAMPLE"] = "second"
        self.assertEqual(captured.to_dict()["env"]["EXAMPLE"], "first")
        for value in (None, {}, {"PWD": "relative"}, {"PWD": "/tmp", "bad=key": "value"}):
            with self.assertRaises(ValueError):
                ShellEnvironment.from_prompt(NS(context=NS(environ=value)))

    def test_submission_captures_before_dispatch_and_preserves_draft_on_error(self):
        with tempfile.TemporaryDirectory() as root:
            view = LiveHubView()
            controller = LiveController(view, HubConfig(root))
            try:
                view.apply_snapshot(HubSnapshot("project", "Project", "test/model", "file",
                    (SessionSummary("session", "Session", "idle"),), "session", ()))
                provider = Mock(return_value=snapshot(root, "initial"))
                controller.environment_provider = provider
                controller._call = Mock()
                view.composer.text = "work"
                controller.submit("session", "work")
                self.assertEqual(view.composer.text, "")
                options = controller._call.call_args.args[4]
                self.assertEqual(options[ENVIRONMENT_OPTION]["env"]["HUB_SHELL_TEST"], "initial")
                self.assertNotIn(ENVIRONMENT_OPTION, view.engine_options)
                controller.refresh_shell_root()
                self.assertEqual(view.file_root, root)
                controller._submitting.clear()
                controller._choosing_submission = False
                controller._error = Mock()
                provider.side_effect = ValueError("missing context")
                view.composer.text = "keep"
                controller.submit("session", "keep")
                self.assertEqual(view.composer.text, "keep")
                controller._error.assert_called_once()
            finally:
                controller.close()

    def test_adapter_preserves_capability_declarations_and_custom_tools(self):
        from llm.engines.base import EngineContext
        from llm.components.tools import Tool, ToolRegistry
        from unittest.mock import AsyncMock
        builtin, custom = Tool("file_read", "read", {"type": "object"}, AsyncMock(spec=lambda arguments: None), contract=TEST_CONTRACT), Tool(
            "custom", "custom", {"type": "object"}, AsyncMock(spec=lambda arguments: None), contract=TEST_CONTRACT)
        bound = Tool("file_read", "read", {"type": "object"}, AsyncMock(spec=lambda arguments: None), contract=TEST_CONTRACT)
        component = NS(name="builtin_tools", resolve_environment=Mock(return_value=ToolRegistry((bound,))))
        engine = EnvironmentEngine(NS(), component, snapshot("/tmp", "test"))
        context = EngineContext(NS(components=["builtin_tools"]), NS(), NS(), ())
        self.assertEqual(engine.bind_context(context).capabilities, {})
        component.resolve_environment.assert_not_called()
        from dataclasses import replace
        context = replace(context, tools=ToolRegistry((builtin, custom)), capabilities={"tools": None})
        result = engine.bind_context(context)
        self.assertIs(result.tools.get("custom").handler, custom.handler)
        self.assertIs(result.tools.get("file_read").handler, bound.handler)
        self.assertEqual(result.tools.names(), context.tools.names())


class ShellExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_absolute_paths_inside_cwd_work_without_relaxing_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root) / "work"
            work.mkdir()
            runtime = HubRuntime(HubConfig(Path(root) / "storage", file_root=work, auto_title=False))
            try:
                await runtime.start()
                registry = runtime.builtin_tools.resolve_environment(
                    await runtime.project.aget_data(), snapshot(work, "paths"))
                args = {"path": str(work / "created.txt"), "content": "created"}
                await registry.get("file_create").handler(args)
                self.assertEqual(args["path"], str(work / "created.txt"))
                read = await registry.get("file_read").handler({"path": str(work / "created.txt")})
                self.assertEqual(read["text"], "created")
                await registry.get("file_move").handler({"path": str(work / "created.txt"),
                    "destination": str(work / "renamed.txt"), "expected_sha256": read["sha256"]})
                self.assertEqual((work / "renamed.txt").read_text(), "created")
                output = await registry.get("shell_execute").handler({"command": "pwd", "cwd": str(work)})
                self.assertEqual(output["stdout"].strip(), str(work))
                for path in (str(Path(root) / "outside.txt"), "../outside.txt", str(work / "../outside.txt")):
                    with self.subTest(path=path), self.assertRaisesRegex(ValueError, "working root"):
                        await registry.get("file_create").handler({"path": path, "content": "blocked"})
                (work / "link").symlink_to(Path(root), target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    await registry.get("file_create").handler({"path": str(work / "link/escape.txt"), "content": "blocked"})
                self.assertFalse(Path(root, "outside.txt").exists())
                self.assertFalse(Path(root, "escape.txt").exists())
            finally:
                await runtime.close()

    async def test_old_llm_schema_cannot_silently_ignore_shell_environment(self):
        from hub.backend.tools.builtin import HubBuiltinTools
        from llm.components.tools.builtin import BuiltinTools
        from llm.components.tools import ToolRegistry
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as root:
            component = HubBuiltinTools(root)
            def old_catalog(*args, **kwargs):
                toolkit = BuiltinTools(*args, **kwargs)
                tools = []
                for name in toolkit.registry.names():
                    tool = toolkit.registry.get(name)
                    parameters = tool.parameters
                    parameters.get("properties", {}).pop("env", None)
                    tools.append(replace(tool, parameters=parameters))
                toolkit.registry = ToolRegistry(tuple(tools))
                return toolkit
            try:
                with patch("hub.backend.tools.builtin.BuiltinTools", side_effect=old_catalog):
                    with self.assertRaisesRegex(RuntimeError, "Update the llm plugin"):
                        component._create(root, {"PWD": root})
            finally:
                await component.close()

    async def test_graph_tool_and_queued_requests_keep_their_environment(self):
        from llm.components.workflows import WorkflowGraph
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.tool import ToolNode
        gate, started = asyncio.Event(), asyncio.Event()
        async def hold(node):
            started.set()
            await gate.wait()
            return {}
        with tempfile.TemporaryDirectory() as root:
            first, second = Path(root) / "first", Path(root) / "second"
            first.mkdir(); second.mkdir()
            (first / "name.txt").write_text("first")
            (second / "name.txt").write_text("second")
            config = HubConfig(Path(root) / "storage", auto_title=False, engine="graph",
                engine_factories={"graph": lambda: GraphEngine(handlers={"hold": hold, "tool": ToolNode()})})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                workflow = (WorkflowGraph(entry="hold").node("hold", "hold")
                    .node("read", "tool", tool="file_read", arguments={"path": "name.txt"})
                    .node("finish", "end").connect("hold", "read").connect("read", "finish"))
                await (await runtime.project.components.aget("workflows")).acreate(workflow.to_dict(), identifier="reader")
                session = await runtime.project.sessions.acreate("Queued environments")
                one = await session.run.submit("first", engine="graph", engine_options={"workflow": "reader",
                    ENVIRONMENT_OPTION: snapshot(first, "A").to_dict()})
                await asyncio.wait_for(started.wait(), 10)
                options = {"workflow": "reader", ENVIRONMENT_OPTION: snapshot(second, "B").to_dict()}
                two = await session.run.submit("second", engine="graph", engine_options=options)
                options[ENVIRONMENT_OPTION]["cwd"] = str(first)
                gate.set()
                for request, expected in ((one, "first"), (two, "second")):
                    result = await request.wait()
                    self.assertEqual(str(result.data.status), "completed", result.data.error)
                    output = next(s for s in await result.steps.alist() if s.kind == "tool").output.data
                    self.assertIn(expected, json.dumps(output))
            finally:
                gate.set()
                await runtime.close()

    async def test_real_ish_install_reads_context_and_cd_refreshes_completion_root(self):
        from prompt_toolkit.input import create_pipe_input
        from tests.hub.test_ish_integration import Prompt
        from tests.hub.support import SizedOutput
        from hub.hub import install
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, config=HubConfig(root))
            try:
                prompt.context.update("environ", b"PWD=/tmp/first\0VALUE=one\0PATH=/usr/bin:/bin\0")
                controller = installation.controller
                self.assertEqual(controller.environment_provider().cwd, "/tmp/first")
                controller.refresh_shell_root()
                self.assertEqual(installation.view.file_root, "/tmp/first")
                prompt.context.update("environ", b"PWD=/tmp/second\0VALUE=two\0PATH=/usr/bin:/bin\0")
                controller.bind_app(prompt.app)
                self.assertEqual(installation.view.file_root, "/tmp/second")
                self.assertEqual(controller.environment_provider().to_dict()["env"]["VALUE"], "two")
            finally:
                installation.close()

    async def test_files_processes_and_explicit_environment_are_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            storage = Path(root) / "storage"
            first, second = Path(root) / "first", Path(root) / "second"
            first.mkdir(); second.mkdir()
            runtime = HubRuntime(HubConfig(storage, auto_title=False))
            cwd, environ = os.getcwd(), dict(os.environ)
            try:
                await runtime.start()
                project = await runtime.project.aget_data()
                registries = [runtime.builtin_tools.resolve_environment(project, snapshot(path, mark))
                              for path, mark in ((first, "A"), (second, "B"))]
                async def execute(registry):
                    await registry.get("file_create").handler({"path": "result.txt", "content": "local"})
                    return await registry.get("shell_execute").handler({"command": 'printf "%s|%s" "$PWD" "$HUB_SHELL_TEST"'})
                results = await asyncio.gather(*(execute(registry) for registry in registries))
                self.assertEqual([item["stdout"] for item in results], [f"{first}|A", f"{second}|B"])
                self.assertTrue((first / "result.txt").is_file())
                self.assertTrue((second / "result.txt").is_file())
                explicit = await registries[0].get("shell_execute").handler({
                    "command": 'printf "%s|%s" "$HUB_SHELL_TEST" "$OTHER"', "env": {"OTHER": "override"}})
                self.assertEqual(explicit["stdout"], "|override")
                await registries[0].get("shell_execute").handler({"command": "/usr/bin/git init -q"})
                git = await registries[0].get("git_status").handler({})
                self.assertEqual(git["stdout"], "?? result.txt\n")
                start = await registries[0].get("process_start").handler({"argv": ["/usr/bin/env"], "cwd": str(first)})
                toolkit = next(t for t in runtime.builtin_tools.toolkits.values() if t.files.root == first)
                await toolkit.processes.sessions[start["process_id"]]["task"]
                output = await registries[0].get("process_output").handler({"process_id": start["process_id"]})
                self.assertIn("HUB_SHELL_TEST=A", output["stdout"])
                self.assertNotEqual(registries[0].contracts(), registries[1].contracts())
                toolkit = next(t for t in runtime.builtin_tools.toolkits.values() if t.files.root == first)
                sandbox = toolkit.registry.get("test_workspace_execute").handler
                self.assertEqual(sandbox.execution_binding["env"]["HUB_SHELL_TEST"], "A")
                self.assertFalse(sandbox.execution_binding["allow_network"])
                changed = runtime.builtin_tools.resolve_environment(project, snapshot(first, "changed"))
                self.assertNotEqual(registries[0].contracts(), changed.contracts())
                self.assertEqual(registries[0].contracts(),
                    runtime.builtin_tools.resolve_environment(project, snapshot(first, "A")).contracts())
                self.assertEqual(os.getcwd(), cwd)
                self.assertEqual(dict(os.environ), environ)
            finally:
                await runtime.close()

    async def test_approval_resume_uses_persisted_snapshot_and_model_gets_only_cwd(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root) / "work"
            work.mkdir()
            # Keep the approved absolute cwd immutable across restart; adapt
            # to the backend's relative-path contract only at execution.
            model = ScriptedCompletion(
                [chunk(calls=[call(json.dumps({"command": 'printf "%s|%s" "$PWD" "$HUB_SHELL_TEST"',
                    "cwd": str(work)}), name="shell_execute")], finish="tool_calls")],
                [chunk("done", finish="stop")])
            config = HubConfig(Path(root) / "storage", model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=model)})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                session = await runtime.project.sessions.acreate("Environment")
                project_id, session_id = runtime.project.id, session.id
                options = {ENVIRONMENT_OPTION: snapshot(work, "captured-secret-value").to_dict()}
                queued = await session.run.submit("do the work", engine="loop", engine_options=options)
                options[ENVIRONMENT_OPTION]["env"]["HUB_SHELL_TEST"] = "changed"
                paused = await queued.wait()
                self.assertEqual(str(paused.data.status), "paused", paused.data.error)
                request, = await paused.ainteractions()
                self.assertEqual(request.action["arguments"]["cwd"], str(work))
                await paused.arespond(request.respond("approve"))
                paused_id = paused.id
                await runtime.close()
                runtime = HubRuntime(config)
                await runtime.start()
                project = await runtime.backend.projects.aload(project_id)
                session = await project.sessions.aload(session_id)
                done = await (await session.run.resume(paused_id, engine="loop")).wait()
                self.assertEqual(str(done.data.status), "completed", done.data.error)
                output = next(s for s in await done.steps.alist() if s.kind == "tool").output.data
                self.assertEqual(output["stdout"], f"{work}|captured-secret-value")
                first_messages = json.dumps(model.requests[0]["messages"])
                self.assertIn(str(work), first_messages)
                self.assertNotIn("captured-secret-value", first_messages)
            finally:
                await runtime.close()

    async def test_graph_options_and_plain_engine_compatibility(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            try:
                await runtime.start()
                graph = runtime.backend.engines.resolve_request("graph", {
                    "workflow": "chosen", ENVIRONMENT_OPTION: snapshot(root, "graph").to_dict()})
                self.assertIsInstance(graph, EnvironmentEngine)
                self.assertEqual(graph.implementation.workflow, "chosen")
                self.assertEqual(graph.environment.cwd, root)
                with self.assertRaises(ValueError):
                    runtime.backend.engines.resolve_request("loop", {"invalid": True})
                self.assertIsNone(runtime.backend.engines.resolve_request("loop", {}).environment)
            finally:
                await runtime.close()
