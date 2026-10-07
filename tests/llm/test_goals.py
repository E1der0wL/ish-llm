"""목표는 실행 부모가 아닌 선택 리소스이며 기존 승인·저장 경계를 재사용한다."""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

from llm.llm import LargeLanguageModel
from llm.components.goals import GoalComponent
from llm.components.goals.processing import GoalProcessor
from llm.components.processing import CompletionRequest, CompletionMessage
from llm.engines.loop import LoopEngine
from llm.core.models import RunStatus
from tests.llm.test_loop import ScriptedCompletion, chunk, call


def goal(scope=None):
    return {"title": "Work", "objective": "Finish safely", "status": "active",
            "scope": scope or {"type": "project"}, "success_criteria": ["Tests pass"]}


class GoalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.provider = ScriptedCompletion(*[[chunk("done", finish="stop")] for _ in range(10)])
        self.app = LargeLanguageModel(Path(self.temp.name), components=[GoalComponent()],
            engines={"loop": LoopEngine(completion_fn=self.provider).for_agent({"engine": 'loop', "engine_options": LoopEngine.settings_layout.pack({'completion': {"model": "test/model"}})})})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("goals", components=["goals"])
        self.session = await self.project.sessions.acreate("session")
        self.goals = self.project.components.goals

    async def test_crud_scopes_references_and_conflicts(self):
        first = await self.goals.acreate(goal())
        second = await self.goals.acreate(goal({"type": "session", "id": self.session.id}))
        other = await self.project.sessions.acreate("other")
        self.assertNotIn(second, await self.goals.alist(session_id=other.id))
        request = await self.session.run.submit("work", engine="loop")
        await request.wait()
        result = await request.aresult()
        before = deepcopy(result)
        for identifier in (first, second):
            view = await self.goals.asnapshot(identifier)
            linked = await self.goals.alink_run(identifier, self.session.id, result.run_id,
                relation="contributes_to", expected_version=view["version"])
            with self.assertRaises(ValueError):
                await self.goals.aupdate(identifier, {"title": "stale"}, expected_version=view["version"])
            paused = await self.goals.apause(identifier, expected_version=linked["version"])
            resumed = await self.goals.aresume(identifier, expected_version=paused["version"])
            completed = await self.goals.acomplete(identifier, expected_version=resumed["version"])
            self.assertEqual(completed["data"]["status"], "completed")
        self.assertEqual(await request.aresult(), before)
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.goals.alink_run(first, self.session.id, "0" * 32, relation="missing",
                expected_version=(await self.goals.asnapshot(first))["version"])
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.goals.acreate(goal({"type": "session", "id": "0" * 32}))
        view = await self.goals.asnapshot(first)
        await self.goals.aunlink_run(first, self.session.id, result.run_id, expected_version=view["version"])
        await self.goals.adelete(first, expected_version=(await self.goals.asnapshot(first))["version"])
        self.assertNotIn(first, await self.goals.alist())

    async def test_clone_and_reselection(self):
        await self.goals.acreate(goal(), identifier="project_goal")
        await self.goals.acreate(goal({"type": "session", "id": self.session.id}), identifier="session_goal")
        clone = await self.project.aclone(title="clone")
        records = await clone.components.goals.alist()
        self.assertEqual(set(records), {"project_goal"})
        self.assertEqual(records["project_goal"]["run_refs"], [])
        await self.project.components.aremove("goals")
        with self.assertRaises(ValueError):
            await self.goals.alist()
        await self.project.components.aselect(["goals"])
        self.assertEqual(len(await self.goals.alist()), 2)

    async def test_opt_in_reference_is_not_system_prompt(self):
        await self.goals.acreate(goal())
        handle = await self.session.run.submit("current", engine="loop")
        await handle.wait()
        self.assertEqual((await handle.aresult()).status, RunStatus.COMPLETED, (await handle.aresult()).error)
        self.assertEqual(self.provider.requests[-1]["messages"][-1]["content"], "current")
        await self.goals.aconfigure({"config": {"priority": 10}, "policy": {"inject": True}})
        handle = await self.session.run.submit("next", engine="loop")
        await handle.wait()
        self.assertEqual((await handle.aresult()).status, RunStatus.COMPLETED)
        messages = self.provider.requests[-1]["messages"]
        self.assertIn("Active Goal Reference", messages[-1]["content"])
        self.assertFalse(any(m["role"] == "system" for m in messages))
        self.assertEqual((await self.session.aconversation())[-2].content, "next")

    async def test_nested_context_requires_explicit_agent_selection(self):
        await self.goals.acreate(goal())
        context = SimpleNamespace(output_step_id="nested", state={"agent": {"agent_id": "worker"}},
                                  session=SimpleNamespace(id=self.session.id), run=SimpleNamespace(input_message_id="input"))
        settings = {"config": {"priority": 1}, "policy": {"inject": True}}
        for enabled in (False, True):
            if enabled:
                settings["config"]["nested_agent_ids"] = ["worker"]
            request = CompletionRequest({}, [CompletionMessage({"role": "user", "content": "task"}, "input")], 0)
            async for _ in GoalProcessor(self.goals, settings).session(context).prepare(request):
                pass
            self.assertEqual("Active Goal" in request.messages[0].value["content"], enabled)

    async def test_unselected_component_keeps_plain_execution(self):
        project = await self.app.projects.acreate("plain", components=[])
        session = await project.sessions.acreate("plain")
        handle = await session.run.submit("plain", engine="loop")
        await handle.wait()
        self.assertEqual((await handle.aresult()).status, RunStatus.COMPLETED)
        self.assertNotIn("tools", self.provider.requests[-1])
        self.assertFalse((project.paths.root / "goals").exists())
