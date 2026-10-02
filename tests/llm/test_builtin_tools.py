"""기본 Tool의 실제 파일/프로세스 동작, 연결 어댑터, 공통 실행 이력을 검증한다."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.llm.support.runtime_tools import RuntimeTools
from llm.components.agents import AgentComponent
from llm.components.tools import ToolComponent
from llm.components.tools.builtin import BuiltinTools
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.models import RunStatus
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine
from llm.engines.graph.tool import ToolNode
from llm.llm import LargeLanguageModel
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class BuiltinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.tools = BuiltinTools(self.work, allow_commands=True, max_seconds=5, max_output_bytes=500, shell=["/bin/sh", "-c"])
        self.addAsyncCleanup(self.tools.close)

    async def invoke(self, name, **args):
        tool, values = self.tools.registry.prepare(name, json.dumps(args))
        return await tool.handler(values)

    async def test_file_crud_versions_search_and_restore(self):
        created = await self.invoke("file_create", path="src/a.py", content="print('한글')\n")
        read = await self.invoke("file_read", path="src/a.py")
        self.assertEqual(created["sha256"], read["sha256"])
        hits = await self.invoke("file_search", query="한글", recursive=True, case_sensitive=False)
        self.assertEqual(hits["matches"][0]["line"], 1)
        changed = await self.invoke("file_patch", path="src/a.py", expected_sha256=read["sha256"], old_text="한글", new_text="hello")
        with self.assertRaises(ValueError):
            await self.invoke("file_patch", path="src/a.py", expected_sha256=read["sha256"], old_text="hello", new_text="stale")
        await self.invoke("file_move", path="src/a.py", destination="src/b.py", expected_sha256=changed["sha256"])
        deleted = await self.invoke("file_delete", path="src/b.py", expected_sha256=changed["sha256"])
        self.assertFalse((self.work / "src/b.py").exists())
        await self.invoke("file_restore", path="restored.py", trash_id=deleted["trash_id"])
        self.assertEqual((self.work / "restored.py").read_text(), "print('hello')\n")
        listed = await self.invoke("file_list", recursive=True)
        self.assertFalse(any(".llm-trash" in entry["path"] for entry in listed["entries"]))

    async def test_overwrite_ambiguous_patch_and_directory_deletion_rejected(self):
        created = await self.invoke("file_create", path="a", content="x x")
        with self.assertRaises(FileExistsError):
            await self.invoke("file_create", path="a", content="overwritten")
        with self.assertRaises(ValueError):
            await self.invoke("file_patch", path="a", expected_sha256=created["sha256"], old_text="x", new_text="y")
        with self.assertRaises(OSError):
            await self.invoke("file_delete", path=".", expected_sha256=created["sha256"])
        self.assertEqual((self.work / "a").read_text(), "x x")

    async def test_escaping_and_reserved_paths_rejected(self):
        for path in ("../escape", str(self.root / "outside"), "a/../../escape", ".llm-trash/x"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.invoke("file_create", path=path, content="bad")
        self.assertFalse((self.root / "outside").exists())

    async def test_linked_directories_cannot_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.work / "linked"
        link.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises(ValueError):
                await self.invoke("file_create", path="linked/escape", content="bad")
            self.assertEqual((await self.invoke("file_list", recursive=True))["entries"], [])
        finally:
            link.unlink()

    async def test_bounded_file_reads_and_paging(self):
        async with BuiltinTools(self.work, max_file_bytes=8) as limited:
            handler, args = limited.registry.prepare("file_create", json.dumps({"path": "large", "content": "x" * 9}))
            with self.assertRaises(ValueError):
                await handler.handler(args)
        await self.invoke("file_create", path="lines", content="a\nb\nc\n")
        page = await self.invoke("file_read", path="lines", start_line=2, max_lines=1)
        self.assertEqual(page["text"], "b\n")
        self.assertTrue(page["truncated"])

    async def test_commands_are_opt_in_and_configured_checks_are_available(self):
        async with BuiltinTools(self.work, checks={"smoke": [sys.executable, "-c", "print('checked')"]}) as tools:
            self.assertNotIn("shell_execute", tools.registry.names())
            self.assertNotIn("process_start", tools.registry.names())
            handler, args = tools.registry.prepare("test_run", '{"name":"smoke"}')
            result = await handler.handler(args)
            self.assertEqual(result["returncode"], 0)
            self.assertIn("checked", result["stdout"])
            with self.assertRaises(ValueError):
                tools.registry.prepare("test_run", '{"name":"unconfigured"}')

    async def test_process_output_limits_and_nonzero_exit(self):
        started = await self.invoke("process_start", argv=[sys.executable, "-c", "import sys; print('x'*5000); sys.exit(7)"])
        await asyncio.wait_for(asyncio.shield(self.tools.processes.sessions[started["process_id"]]["task"]), 5)
        result = await self.invoke("process_output", process_id=started["process_id"])
        self.assertEqual(result["returncode"], 7)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["stdout"]) + len(result["stderr"]), 500)

    async def test_process_timeout_and_cancel_cleanup(self):
        result = await self.tools.processes.execute({"argv": [sys.executable, "-c", "import time; time.sleep(20)"], "timeout_seconds": 0.1})
        self.assertEqual(result["status"], "timed_out")
        self.assertIsNotNone(result["returncode"])
        started = await self.invoke("process_start", argv=[sys.executable, "-c", "import time; time.sleep(20)"])
        result = await self.invoke("process_cancel", process_id=started["process_id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNotNone(result["returncode"])

    async def test_immediate_shutdown_cleans_process_before_monitor_starts(self):
        started = await self.invoke("process_start", argv=[sys.executable, "-c", "import time; time.sleep(20)"])
        await self.tools.close()
        process = self.tools.processes.sessions[started["process_id"]]["process"]
        self.assertIsNotNone(process.returncode)
        with self.assertRaises(RuntimeError):
            await self.invoke("file_list", recursive=False)

    async def test_caller_cancellation_drains_foreground_process(self):
        pending = asyncio.create_task(self.tools.processes.execute({"argv": [sys.executable, "-c", "import time; time.sleep(20)"]}))
        for _ in range(200):
            if self.tools.processes.sessions:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.tools.processes.sessions)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(all(item["process"].returncode is not None for item in self.tools.processes.sessions.values()))

    async def test_cancel_during_spawn_drains_full_output_pipe(self):
        create = asyncio.create_subprocess_exec
        spawned, release = asyncio.Event(), asyncio.Event()
        children = []
        marker = self.work / "pipe-full"

        async def delayed(*args, **kwargs):
            process = await create(*args, **kwargs)
            children.append(process)
            spawned.set()
            await release.wait()
            return process

        code = ("import os,pathlib,time; "
                f"pathlib.Path({str(marker)!r}).touch(); "
                "os.write(1,b'x'*1000000); time.sleep(60)")
        with patch("llm.components.tools.builtin.processes.asyncio.create_subprocess_exec", delayed):
            pending = asyncio.create_task(self.tools.processes.start({"argv": [sys.executable, "-c", code]}))
            try:
                await asyncio.wait_for(spawned.wait(), 5)
                for _ in range(200):
                    if marker.exists():
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(marker.exists())
                pending.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 8)
                self.assertTrue(all(child.returncode is not None for child in children))
                self.assertEqual(self.tools.processes.sessions, {})
            finally:
                release.set()
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_shell_and_process_cwd(self):
        command = "printf shell-ok"
        result = await self.invoke("shell_execute", command=command)
        self.assertEqual(result["returncode"], 0)
        self.assertIn("shell-ok", result["stdout"])
        with self.assertRaises(ValueError):
            await self.invoke("process_start", argv=[sys.executable, "-V"], cwd="../")

    async def test_adapters_are_bound_explicitly_and_validate_inputs(self):
        names = {"web_search": {"query": "q"}, "web_fetch": {"url": "https://example.test"},
                 "browser_open": {"url": "https://example.test"}, "browser_snapshot": {"session_id": "s"},
                 "browser_act": {"session_id": "s", "action": {"click": "button"}},
                 "kernel_execute": {"session_id": "s", "code": "1+1"}, "kernel_reset": {"session_id": "s"},
                 "external_rag_search": {"corpus": "c", "query": "q"}, "external_graphrag_search": {"corpus": "c", "query": "q"},
                 "context_expand": {"reference": "r", "scope": "section"},
                 "harness_propose_update": {"harness_id": "h", "changes": {"prompt": "new"}, "reason": "test"}}
        async def adapter(args):
            return {"received": args}
        async with BuiltinTools(self.work, adapters={name: adapter for name in names}) as tools:
            for name, arguments in names.items():
                self.assertNotIn(name, self.tools.registry.names())
                tool, values = tools.registry.prepare(name, json.dumps(arguments))
                self.assertEqual(await tool.handler(values), {"received": arguments})
            with self.assertRaises(ValueError):
                tools.registry.prepare("web_fetch", '{"url":"file:///outside"}')

    async def test_legacy_search_adapter_names_require_explicit_migration(self):
        async def adapter(args):
            return args
        for name in ("rag_search", "graphrag_search"):
            with self.assertRaisesRegex(ValueError, "Update the adapter key and Project enabled tool names"):
                BuiltinTools(self.work, adapters={name: adapter})

    async def test_prompt_updates_compare_revision_and_keep_other_settings(self):
        async with LargeLanguageModel(self.root / "backend", components=[AgentComponent()]) as backend:
            project = await backend.projects.acreate("prompts", components=["agents"])
            agents = await project.components.aget("agents")
            original = {"engine": "loop", "purpose": "test", "system_prompt": "old", "completion": {"model": "demo"}, "policies": {"limit": 3}}
            await agents.acreate(original, identifier="a")
            self.tools.bind_prompts(agents)
            read = await self.invoke("prompt_read", agent_id="a")
            updated = await self.invoke("prompt_update", agent_id="a", system_prompt="new", expected_revision=read["revision"])
            self.assertEqual(updated["applies_to"], "subsequent_runs")
            self.assertEqual((await agents.aload("a"))["policies"], original["policies"])
            with self.assertRaises(ValueError):
                await self.invoke("prompt_update", agent_id="a", system_prompt="stale", expected_revision=read["revision"])
        with self.assertRaises(RuntimeError):
            await self.invoke("prompt_read", agent_id="a")

    async def test_loop_executes_file_tool_and_persists_arguments_result(self):
        arguments = {"path": "loop.txt", "content": "created by Loop"}
        completion = ScriptedCompletion([chunk(calls=[call(json.dumps(arguments), name="file_create")]), chunk(finish="tool_calls")],
                                         [chunk("done"), chunk(finish="stop")])
        async with LargeLanguageModel(self.root / "backend", components=[RuntimeTools(self.tools.registry)],
                                      engines={"loop": LoopEngine(completion_fn=completion, completion_kwargs={"model": "demo"})}) as backend:
            project = await backend.projects.acreate("tools", components=["tools"])
            selected = await project.components.aget("tools")
            await selected.aenable("file_create")
            session = await project.sessions.acreate()
            run = await (await session.run.submit("create", engine="loop")).wait()
            self.assertEqual(run.data.status, RunStatus.COMPLETED)
            step = next(step for step in await run.steps.alist() if step.kind == "tool")
            self.assertEqual(step.metadata["arguments"], arguments)
            self.assertIn("sha256", step.output.data)
        self.assertEqual((self.work / "loop.txt").read_text(), "created by Loop")

    async def test_graph_tool_node_uses_same_executor_and_dynamic_arguments(self):
        graph = (WorkflowGraph(entry="create", initial_state={"args": {"path": "graph.txt", "content": "Graph"}})
                 .node("create", "tool", tool="file_create", arguments_key="args", result_key="created")
                 .node("end", "end").connect("create", "end").to_dict())
        async with LargeLanguageModel(self.root / "backend", components=[RuntimeTools(self.tools.registry), WorkflowComponent()],
                                      engines={"graph": GraphEngine("g", handlers={"tool": ToolNode()})}) as backend:
            project = await backend.projects.acreate("tools", components=["tools", "workflows"])
            await (await project.components.aget("tools")).aenable("file_create")
            await (await project.components.aget("workflows")).acreate(graph, identifier="g")
            session = await project.sessions.acreate()
            run = await (await session.run.submit("create", engine="graph")).wait()
            self.assertEqual(run.data.status, RunStatus.COMPLETED)
            steps = await run.steps.alist()
            tool = next(step for step in steps if step.kind == "tool")
            root = next(step for step in steps if step.kind == "graph")
            self.assertEqual(root.output.data["created"], tool.output.data)

    @unittest.skipUnless(shutil.which("git"), "Git is unavailable")
    async def test_git_read_tools(self):
        result = await self.tools.processes.execute({"argv": ["git", "init"]})
        self.assertEqual(result["returncode"], 0)
        await self.invoke("file_create", path="new.txt", content="untracked")
        async with BuiltinTools(self.work, git=True) as tools:
            handler, args = tools.registry.prepare("git_status", "{}")
            self.assertIn("new.txt", (await handler.handler(args))["stdout"])
            handler, args = tools.registry.prepare("git_diff", "{}")
            self.assertEqual((await handler.handler(args))["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
