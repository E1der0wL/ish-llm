"""Deterministic model, real approved tools, real failing/fixed sandbox tests."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
import asyncio
from threading import Event
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class CodingSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_skill_query_yields_to_backend_observations(self):
        from hub.backend.skills import HubSkillComponent
        from llm.components.skills import SkillComponent
        from llm.components.tools import Tool, ToolRegistry, ToolContract
        from tests.hub.support import eventually
        entered, release = Event(), Event()
        async def listing(arguments):
            entered.set()
            release.wait(3)
            return {"skills": []}
        original = ToolRegistry((Tool("skill_list", "list", {"type": "object"}, listing,
            contract=ToolContract(effect="read_only", revision="snapshot")),))
        with patch.object(SkillComponent, "resolve", return_value=original):
            tools = HubSkillComponent().resolve(None, "tools")
        self.assertEqual(tools.contracts(), original.contracts())
        task = asyncio.create_task(tools.get("skill_list").handler({}))
        try:
            await eventually(entered.is_set)
            self.assertFalse(task.done())
            self.assertFalse(release.is_set())
        finally:
            release.set()
            await task

    async def test_skill_reproduces_and_repairs_in_separate_workspace(self):
        provider = ScriptedCompletion()
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            source.mkdir()
            original = "def add(a, b):\n    return a - b\n"
            (source / "calculator.py").write_text(original)
            runtime = HubRuntime(HubConfig(Path(root) / "hub", file_root=source,
                model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                installed = await runtime.component_command(runtime.project.id, "skills", "install hub-code-change coding-tested")
                self.assertEqual(installed["created"], "coding-tested")
                with self.assertRaises((FileExistsError, ValueError)):
                    await runtime.component_command(runtime.project.id, "skills", "install hub-code-change coding-tested")
                session = await runtime.project.sessions.acreate("Coding test")

                async def tool(name, **arguments):
                    provider.responses.extend([
                        [chunk(calls=[call(json.dumps(arguments), name=name)], finish="tool_calls")],
                        [chunk("Tool result checked", finish="stop")]])
                    result = await (await session.run.submit(name, engine="loop")).wait()
                    if str(result.data.status) == "paused":
                        request, = await result.ainteractions()
                        await result.arespond(request.respond("approve"))
                        result = await (await session.run.resume(result.id, engine="loop")).wait()
                    self.assertEqual(str(result.data.status), "completed", result.data.error)
                    return next(step.output.data for step in await result.steps.alist() if step.kind == "tool")

                listing = await tool("skill_list")
                self.assertIn("hub-code-change", json.dumps(listing))
                guide = await tool("skill_read", identifier="hub-code-change")
                self.assertIn("test_workspace_execute", json.dumps(guide))
                created = await tool("test_workspace_create", source_paths=["calculator.py"])
                workspace = created["workspace_id"]
                await tool("file_create", workspace_id=workspace, path="test_calculator.py", content=
                    "import unittest\nfrom calculator import add\n"
                    "class Check(unittest.TestCase):\n"
                    "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
                failed = await tool("test_workspace_execute", workspace_id=workspace,
                    argv=[str(Path(sys.executable).resolve()), "-m", "unittest", "test_calculator", "-v"])
                self.assertNotEqual(failed["returncode"], 0)
                self.assertIn("AssertionError", failed["stderr"])
                read = await tool("file_read", workspace_id=workspace, path="calculator.py")
                await tool("file_patch", workspace_id=workspace, path="calculator.py",
                    expected_sha256=read["sha256"], old_text="return a - b", new_text="return a + b")
                passed = await tool("test_workspace_execute", workspace_id=workspace,
                    argv=[str(Path(sys.executable).resolve()), "-m", "unittest", "test_calculator", "-v"])
                self.assertEqual(passed["returncode"], 0, passed)
                self.assertIn("OK", passed["stderr"])
                self.assertEqual((source / "calculator.py").read_text(), original)
                self.assertFalse((source / "test_calculator.py").exists())
                await tool("test_workspace_delete", workspace_id=workspace)
            finally:
                await runtime.close()
