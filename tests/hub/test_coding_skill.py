"""Deterministic model, real approved tools, real failing/fixed sandbox tests."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import json
from pathlib import Path
import sys
import tempfile
import unittest
import asyncio
from threading import Event
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from hub.asset.guides import SKILLS, SYSTEM_PROMPT
from hub.config.environment import ENVIRONMENT_OPTION, ShellEnvironment
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, call, chunk
from llm.components.tools import ToolEffect, ToolIsolation


class CodingSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_requested_file_is_saved_and_read_back_in_shell_directory(self):
        """Exercise delivery plumbing; scripted choices do not measure model compliance."""
        content = "def greet(name):\n    return f'Hello, {name}!'\n"
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({"identifier": "hub-code-change"}), name="skill_read", call_id="guide")],
                   finish="tool_calls")],
            [chunk(calls=[call(json.dumps({"path": "greeting.py", "content": content}),
                              name="file_create", call_id="write")], finish="tool_calls")],
            [chunk(calls=[call(json.dumps({"path": "greeting.py"}), name="file_read", call_id="verify")],
                   finish="tool_calls")],
            [chunk("greeting.py를 작성하고 저장된 내용을 확인했습니다.", finish="stop")],
            [chunk("예시: greet('Hub')", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            work, fallback = Path(root) / "work", Path(root) / "fallback"
            work.mkdir()
            fallback.mkdir()
            (work / "keep.txt").write_text("user work", encoding="utf-8")
            runtime = HubRuntime(HubConfig(Path(root) / "storage", file_root=fallback,
                model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                session = await runtime.project.sessions.acreate("File delivery", agent_ids=sorted(await runtime.project.agents.alist()))
                options = {ENVIRONMENT_OPTION: ShellEnvironment(str(work), ()).to_dict()}
                result = (await (await session.run.submit(
                    "현재 디렉토리에 인사 함수를 greeting.py로 작성해줘.", recipients=["assistant"], request_context=options)).wait())[0]
                self.assertEqual(str(result.data.status), "paused")
                self.assertFalse((work / "greeting.py").exists())
                request, = await result.ainteractions(pending_only=True)
                await result.arespond(request.respond("approve"))
                result = (await (await session.run.resume(result.id, agent_id="assistant")).wait())[0]
                self.assertEqual(str(result.data.status), "completed", result.data.error)
                self.assertEqual((work / "greeting.py").read_text(encoding="utf-8"), content)
                self.assertFalse((fallback / "greeting.py").exists())
                self.assertFalse(list((Path(root) / "storage").rglob("greeting.py")))
                self.assertEqual(provider.requests[0]["messages"][0]["content"], SYSTEM_PROMPT)
                messages = provider.requests[-1]["messages"]
                guide = next(body for identifier, _, _, body in SKILLS if identifier == "hub-code-change")
                tool_results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
                self.assertTrue(any(value.get("definition", {}).get("markdown") == guide for value in tool_results))
                self.assertTrue(any(value.get("text") == content for value in tool_results))
                # Chat-only examples can complete without manufacturing a file operation.
                response = (await (await session.run.submit("파일은 바꾸지 말고 사용 예시만 보여줘.",
                    recipients=["assistant"], request_context=options)).wait())[0]
                self.assertEqual(str(response.data.status), "completed", response.data.error)
                self.assertFalse(any(step.kind == "tool" for step in await response.steps.alist()))
                self.assertEqual({path.name for path in work.iterdir()}, {"greeting.py", "keep.txt"})
                self.assertEqual((work / "keep.txt").read_text(encoding="utf-8"), "user work")
            finally:
                await runtime.close()

    async def test_skill_query_yields_to_backend_observations(self):
        from hub.backend.tools.skills import HubSkillComponent
        from llm.components.skills import SkillComponent
        from llm.components.tools import Tool, ToolRegistry, ToolContract
        from tests.hub.support import eventually
        entered, release = Event(), Event()
        async def listing(arguments):
            entered.set()
            release.wait(3)
            return {"skills": []}
        original = ToolRegistry((Tool("skill_list", "list", {"type": "object"}, listing,
            contract=ToolContract(revision='snapshot', effect=ToolEffect.READ_ONLY, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=False, retry_fields=())),))
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
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                skills = await runtime.project.components.aget("skills")
                definition = await skills.aload("hub-code-change")
                from llm.components.skills import SkillRecord
                definition = SkillRecord.renamed(definition, "coding-tested")
                installed = await skills.acreate(definition, identifier="coding-tested")
                self.assertEqual(installed, "coding-tested")
                with self.assertRaises((FileExistsError, ValueError)):
                    await skills.acreate(definition, identifier="coding-tested")
                session = await runtime.project.sessions.acreate("Coding test", agent_ids=sorted(await runtime.project.agents.alist()))

                async def tool(name, **arguments):
                    provider.responses.extend([
                        [chunk(calls=[call(json.dumps(arguments), name=name)], finish="tool_calls")],
                        [chunk("Tool result checked", finish="stop")]])
                    result = (await (await session.run.submit(name, recipients=["assistant"])).wait())[0]
                    if str(result.data.status) == "paused":
                        request, = await result.ainteractions()
                        await result.arespond(request.respond("approve"))
                        result = (await (await session.run.resume(result.id, agent_id="assistant")).wait())[0]
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
                # A validated copy is not delivery: apply the scoped patch and new test to the original.
                original_read = await tool("file_read", path="calculator.py")
                await tool("file_patch", path="calculator.py", expected_sha256=original_read["sha256"],
                           old_text="return a - b", new_text="return a + b")
                test_read = await tool("file_read", workspace_id=workspace, path="test_calculator.py")
                await tool("file_create", path="test_calculator.py", content=test_read["text"])
                delivered = await tool("file_read", path="calculator.py")
                self.assertEqual(delivered["text"], original.replace("return a - b", "return a + b"))
                self.assertEqual((source / "test_calculator.py").read_text(), test_read["text"])
                process = await asyncio.create_subprocess_exec(sys.executable, "-m", "unittest", "test_calculator", "-v",
                    cwd=source, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()
                self.assertEqual(process.returncode, 0, (stdout, stderr))
                self.assertIn(b"OK", stderr)
                await tool("test_workspace_delete", workspace_id=workspace)
            finally:
                await runtime.close()
