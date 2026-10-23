"""목표는 실행 부모가 아닌 선택 리소스이며 기존 승인·저장 경계를 재사용한다."""

from llm.core.model_protocol import ModelMessage

from llm.providers.litellm import LiteLLMCompletionAdapter

import tempfile
import json
import unittest
from unittest.mock import patch
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
from llm.services.composition import BackendServices
from llm.services.runtime.tools import ToolRuntime, ToolApprovalRequired
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
            engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=self.provider))})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("goals", components=["goals"])
        await self.project.agents.acreate({"engine": "loop", "completion": {"model": "test/model"}}, identifier="loop")
        self.session = await self.project.sessions.acreate("session", agent_ids=sorted(await self.project.agents.alist()))
        self.goals = self.project.components.goals

    async def test_crud_scopes_references_and_conflicts(self):
        first = await self.goals.acreate(goal())
        second = await self.goals.acreate(goal({"type": "session", "id": self.session.id}))
        other = await self.project.sessions.acreate("other", agent_ids=sorted(await self.project.agents.alist()))
        self.assertNotIn(second, await self.goals.alist(session_id=other.id))
        request = await self.session.run.submit("work", recipients=["loop"])
        await request.wait()
        result = (await request.aresults())[0]
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
        self.assertEqual((await request.aresults())[0], before)
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
        handle = await self.session.run.submit("current", recipients=["loop"])
        await handle.wait()
        self.assertEqual((await handle.aresults())[0].status, RunStatus.COMPLETED, (await handle.aresults())[0].error)
        self.assertEqual(self.provider.requests[-1]["messages"][-1]["content"], "current")
        await self.goals.aconfigure({"config": {"priority": 10}, "policy": {"inject": True}})
        handle = await self.session.run.submit("next", recipients=["loop"])
        await handle.wait()
        self.assertEqual((await handle.aresults())[0].status, RunStatus.COMPLETED)
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
            request = CompletionRequest({}, [CompletionMessage(ModelMessage("user", "task"), "input")], 0)
            async for _ in GoalProcessor(self.goals, settings).session(context).prepare(request):
                pass
            self.assertEqual("Active Goal" in request.messages[0].message.text, enabled)

    async def test_unselected_component_keeps_plain_execution(self):
        project = await self.app.projects.acreate("plain", components=[])
        await project.agents.acreate({"engine": "loop", "completion": {"model": "test/model"}}, identifier="loop")
        session = await project.sessions.acreate("plain", agent_ids=sorted(await project.agents.alist()))
        handle = await session.run.submit("plain", recipients=["loop"])
        await handle.wait()
        self.assertEqual((await handle.aresults())[0].status, RunStatus.COMPLETED)
        self.assertNotIn("tools", self.provider.requests[-1])
        self.assertFalse((project.paths.root / "goals").exists())

    async def test_progress_tool_approval_scope_and_stale_version_preserve_storage(self):
        async def authorize(call):
            raise ToolApprovalRequired("Review Goal progress mutation")

        for scenario in ("approved", "stale", "other_session"):
            with self.subTest(scenario=scenario):
                model = ScriptedCompletion()
                component = GoalComponent()
                async with LargeLanguageModel(Path(self.temp.name) / scenario, components=[component],
                        engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},
                        services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize))) as app:
                    project = await app.projects.acreate(components=["goals"])
                    await project.agents.acreate({"engine": "loop", "completion": {"model": "test/model"},
                        "tools": ["goal_update_progress"]}, identifier="loop")
                    session = await project.sessions.acreate(agent_ids=["loop"])
                    goals = project.components.goals
                    await goals.aconfigure({"policy": {"write_tools": True}})
                    scope = None
                    if scenario == "other_session":
                        other = await project.sessions.acreate(agent_ids=["loop"])
                        scope = {"type": "session", "id": other.id}
                    identifier = await goals.acreate(goal(scope))
                    before = await goals.asnapshot(identifier)
                    model.responses.extend([
                        [chunk(calls=[call(json.dumps({"identifier": identifier,
                            "expected_version": before["version"], "changes": {"progress": ["approved progress"]}}),
                            name="goal_update_progress")], finish="tool_calls")],
                        [chunk("done", finish="stop")]])
                    paused = (await (await session.run.submit("update", recipients=["loop"])).wait())[0]
                    self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
                    self.assertEqual(await goals.asnapshot(identifier), before)
                    request, = await paused.ainteractions(pending_only=True)
                    self.assertTrue(request.action["contract"]["approval_required"])
                    await paused.arespond(request.respond("approve"))
                    if scenario == "stale":
                        await goals.aupdate(identifier, {"progress": ["newer saved progress"]},
                                            expected_version=before["version"])
                    protected = await goals.asnapshot(identifier)
                    original_save = component.save
                    with patch.object(component, "save", wraps=original_save) as writes:
                        resumed = (await (await session.run.resume(paused.id, agent_id="loop")).wait())[0]
                        self.assertEqual(resumed.data.status,
                            RunStatus.COMPLETED if scenario == "approved" else RunStatus.FAILED, resumed.data.error)
                        if scenario != "approved":
                            self.assertIn("edit_conflict" if scenario == "stale" else "outside the current Session scope",
                                          resumed.data.error)
                        self.assertEqual(writes.call_count, 1 if scenario == "approved" else 0)
                    after = await goals.asnapshot(identifier)
                    if scenario == "approved":
                        self.assertEqual(after["data"]["progress"], ["approved progress"])
                        self.assertNotEqual(after["version"], before["version"])
                    else:
                        self.assertEqual(after, protected)
                    self.assertEqual(len(model.requests), 2 if scenario == "approved" else 1)
