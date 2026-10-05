"""제안/승인/단일 대상 CAS/되돌리기 및 실제 Run·Tool 실행 통합."""

import json
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.refinement import RefinementComponent
from llm.components.skills import SkillComponent
from llm.components.prompts import PromptComponent
from llm.components.agents import AgentComponent
from llm.components.memory import MemoryComponent
from llm.components.goals import GoalComponent
from llm.engines.loop import LoopEngine
from llm.services.configuration import ServiceConfig
from llm.services.runtime.tools import ToolPolicy, ToolApprovalRequired
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm.test_goals import goal
from tests.llm.test_work_state import work_state
from tests.llm.configuration_fixtures import memory_settings, memory_processing


class RefinementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prompt_component = PromptComponent()
        self.memory_component = MemoryComponent(completion_fn=lambda **kw: iter([
            chunk(json.dumps({"work_state": work_state()}), finish="stop")]))
        self.parts = [RefinementComponent(), SkillComponent(), self.prompt_component,
                      AgentComponent(), self.memory_component, GoalComponent()]
        async def authorize(call):
            if call.name in ("refinement_apply", "refinement_rollback"):
                raise ToolApprovalRequired("Review exact resource change")
            return True
        self.model = ScriptedCompletion(*[[chunk("work", finish="stop")] for _ in range(20)])
        self.app = LargeLanguageModel(self.root, components=self.parts, engines={"loop": LoopEngine(
            completion_fn=self.model, completion_kwargs={"model": "test/model"})},
            services=ServiceConfig(tool_policy=ToolPolicy(authorize=authorize)))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("work", components=[p.name for p in self.parts])
        self.session = await self.project.sessions.acreate()
        self.refine = self.project.components.refinement
        self.run = await (await self.session.run.submit("baseline", engine="loop")).wait()
        self.evidence = [{"session_id": self.session.id, "run_id": self.run.id}]
        await self.project.components.skills.acreate({"instructions": "Before"}, identifier="guide")
        await self.project.components.prompts.acreate({"messages": [{"role": "system", "content": "Before"}]}, identifier="guide")
        await self.project.components.agents.acreate({"purpose": "Before", "engine": "loop"}, identifier="guide")
        await self.project.components.memory.acreate({"content": "Before"}, identifier="guide")

    async def proposal(self, component="skills", changes=None):
        target = {"component": component, "identifier": "guide"}
        current = await self.refine.atarget_snapshot(target)
        data = {"target": target, "operation": "update", "expected_version": current["version"],
                "reason": "Observed missing checks", "evidence": self.evidence,
                "patch": changes or {"instructions": "After: inspect validation errors"}}
        return await self.refine.acreate(data), data, current

    async def approve(self, identifier):
        view = await self.refine.asnapshot(identifier)
        return await self.refine.aapprove(identifier, expected_version=view["version"])

    async def test_skill_prompt_agent_apply_rollback_and_no_implicit_mutation(self):
        for component, changes in (("skills", {"instructions": "After"}),
            ("prompts", {"messages": [{"role": "system", "content": "After"}]}),
            ("agents", {"purpose": "After", "system_prompt": "Check evidence"})):
            with self.subTest(component=component):
                identifier, data, original = await self.proposal(component, changes)
                self.assertEqual(await self.refine.atarget_snapshot(data["target"]), original)
                proposal = await self.refine.asnapshot(identifier)
                self.assertEqual(proposal["data"]["evidence"], self.evidence)
                with self.assertRaisesRegex(ValueError, "status"):
                    await self.refine.aapply(identifier, expected_version=proposal["version"])
                approved = await self.approve(identifier)
                applied = await self.refine.aapply(identifier, expected_version=approved["version"])
                self.assertEqual(applied["data"]["status"], "applied")
                self.assertNotEqual(applied["data"]["applied_target"]["version"], original["version"])
                reverted = await self.refine.arollback(identifier, expected_version=applied["version"])
                self.assertEqual(reverted["data"]["status"], "rolled_back")
                self.assertEqual(await self.refine.atarget_snapshot(data["target"]), original)

    async def test_stale_apply_and_stale_rollback_never_overwrite(self):
        identifier, data, original = await self.proposal()
        approved = await self.approve(identifier)
        skills = self.project.components.skills
        await skills.aupdate("guide", {"instructions": "User change"}, expected_version=original["version"])
        with self.assertRaises(ValueError):
            await self.refine.aapply(identifier, expected_version=approved["version"])
        self.assertNotEqual((await self.refine.aload(identifier))["status"], "applied")
        identifier, _, _ = await self.proposal()
        approved = await self.approve(identifier)
        applied = await self.refine.aapply(identifier, expected_version=approved["version"])
        await skills.aupdate("guide", {"instructions": "Latest user change"},
                            expected_version=applied["data"]["applied_target"]["version"])
        with self.assertRaises(ValueError):
            await self.refine.arollback(identifier, expected_version=applied["version"])
        self.assertEqual((await skills.aload("guide"))["instructions"], "Latest user change")

    async def test_memory_creates_review_candidate_without_overwriting_confirmed(self):
        identifier, data, original = await self.proposal("memory", {"content": "After"})
        approved = await self.approve(identifier)
        applied = await self.refine.aapply(identifier, expected_version=approved["version"])
        memory = self.project.components.memory
        self.assertEqual(await self.refine.atarget_snapshot(data["target"]), original)
        candidate_id = applied["data"]["applied_target"]["identifier"]
        candidate = await memory.aload(candidate_id)
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["metadata"]["replaces"], [{"id": "guide", "revision": 1}])
        self.assertIn(candidate, (await memory.areview())["proposals"])
        await self.refine.arollback(identifier, expected_version=applied["version"])
        self.assertTrue((await memory.aload(candidate_id, include_deleted=True))["deleted"])
        self.assertEqual(await self.refine.atarget_snapshot(data["target"]), original)

    async def test_consolidated_memory_cannot_be_rolled_back_as_unchanged_candidate(self):
        identifier, _, _ = await self.proposal("memory", {"content": "After"})
        approved = await self.approve(identifier)
        applied = await self.refine.aapply(identifier, expected_version=approved["version"])
        candidate_id = applied["data"]["applied_target"]["identifier"]
        await self.project.components.memory.aconsolidate(candidate_id, expected_revision=1)
        with self.assertRaises(ValueError):
            await self.refine.arollback(identifier, expected_version=applied["version"])

    async def test_validation_failure_and_save_failure_do_not_mark_applied(self):
        with self.assertRaises(ValueError):
            await self.proposal("prompts", {"messages": []})
        identifier, data, original = await self.proposal("prompts", {"messages": [{"role": "user", "content": "After"}]})
        approved = await self.approve(identifier)
        with patch.object(self.prompt_component, "save", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                await self.refine.aapply(identifier, expected_version=approved["version"])
        self.assertEqual((await self.refine.aload(identifier))["status"], "approved")
        self.assertEqual(await self.refine.atarget_snapshot(data["target"]), original)

    async def test_proposal_receipt_failure_rolls_back_target_write(self):
        identifier, data, original = await self.proposal()
        approved = await self.approve(identifier)
        with patch.object(self.parts[0], "save", side_effect=OSError("receipt write failed")):
            with self.assertRaises(OSError):
                await self.refine.aapply(identifier, expected_version=approved["version"])
        self.assertEqual(await self.refine.atarget_snapshot(data["target"]), original)
        self.assertEqual((await self.refine.aload(identifier))["status"], "approved")

    async def test_cross_project_evidence_and_privileged_targets_are_rejected(self):
        _, data, _ = await self.proposal()
        for component in ("runtime", "base_prompt", "tools", "goals"):
            bad = deepcopy(data)
            bad["target"]["component"] = component
            with self.assertRaises(ValueError):
                await self.refine.acreate(bad)
        for changes in ({"policy": {"require_tool": False}}, {"engine": "other"}, {"tools": ["shell"]}):
            with self.assertRaises(ValueError):
                await self.proposal("agents", changes)
        other = await self.app.projects.acreate("other", components=["skills"])
        other_session = await other.sessions.acreate()
        other_run = await (await other_session.run.submit("other", engine="loop")).wait()
        data["evidence"] = [{"session_id": other_session.id, "run_id": other_run.id}]
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.refine.acreate(data)
        data["target"]["project_id"] = other.id
        with self.assertRaises(ValueError):
            await self.refine.acreate(data)

    async def test_reject_and_immutable_proposal(self):
        identifier, _, _ = await self.proposal()
        view = await self.refine.asnapshot(identifier)
        for method in (self.refine.asave, self.refine.aupdate):
            with self.assertRaises(ValueError):
                await method(identifier, {"status": "approved"})
        rejected = await self.refine.areject(identifier, expected_version=view["version"])
        with self.assertRaises(ValueError):
            await self.refine.aapply(identifier, expected_version=rejected["version"])

    async def test_approval_pauses_before_apply_and_resume_applies_exactly_once(self):
        identifier, data, original = await self.proposal()
        view = await self.refine.asnapshot(identifier)
        await self.refine.aconfigure({"policy": {"apply_tools": True}})
        model = ScriptedCompletion([chunk(calls=[call(json.dumps({"identifier": identifier,
            "expected_version": view["version"]}), name="refinement_apply")], finish="tool_calls")],
            [chunk("applied", finish="stop")])
        self.app.engines.register("refine", LoopEngine(completion_fn=model, completion_kwargs={"model": "test/model"}))
        paused = await (await self.session.run.submit("apply", engine="refine")).wait()
        self.assertEqual(str(paused.data.status), "paused", paused.data.error)
        self.assertEqual(await self.refine.atarget_snapshot(data["target"]), original)
        self.assertEqual((await self.refine.aload(identifier))["status"], "proposed")
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond("approve"))
        resumed = await (await self.session.run.resume(paused.id, engine="refine")).wait()
        self.assertEqual(str(resumed.data.status), "completed", resumed.data.error)
        proposal = await self.refine.aload(identifier)
        self.assertEqual([h["status"] for h in proposal["history"]], ["proposed", "approved", "applied"])
        self.assertEqual(proposal["history"][-1]["source"]["run_id"], resumed.id)

    async def test_reopen_preserves_proposals_and_clone_has_no_source_approvals(self):
        identifier, _, _ = await self.proposal()
        approved = await self.approve(identifier)
        await self.session.run.shutdown()
        copied = await self.project.aclone(title="copy")
        self.assertEqual(await copied.components.refinement.alist(), {})
        await self.app.shutdown()
        app = LargeLanguageModel(self.root, components=self.parts)
        self.addAsyncCleanup(app.shutdown)
        reopened = await app.projects.aload(self.project.id)
        self.assertEqual(await reopened.components.refinement.asnapshot(identifier), approved)

    async def test_explicit_failure_and_config_opt_in(self):
        identifier, _, _ = await self.proposal()
        view = await self.refine.asnapshot(identifier)
        failed = await self.refine.afail(identifier, expected_version=view["version"], reason="Validator rejected this approach")
        self.assertEqual(failed["data"]["status"], "failed")
        self.assertEqual(await self.refine.aconfiguration(), {})
        tool_names = [tool["function"]["name"] for tool in self.model.requests[0]["tools"]]
        self.assertNotIn("refinement_apply", tool_names)

    async def test_goal_compaction_error_refinement_and_followup_run(self):
        goals = self.project.components.goals
        await goals.acreate(goal(), identifier="work")
        read_call = [chunk(calls=[call('{"identifier":"guide"}', name="skill_read")], finish="tool_calls")]
        first_model = ScriptedCompletion(read_call, [chunk("first completed", finish="stop")])
        self.app.engines.register("first", LoopEngine(completion_fn=first_model, completion_kwargs={"model": "test/main"}))
        first = await (await self.session.run.submit("first work", engine="first")).wait()
        self.assertEqual(str(first.data.status), "completed", first.data.error)
        original_steps = deepcopy(await first.steps.alist())
        original_result = deepcopy(await first.aresult())
        raw_conversation = deepcopy(await self.session.aconversation())
        view = await goals.asnapshot("work")
        await goals.alink_run("work", self.session.id, first.id, relation="contributes_to", expected_version=view["version"])
        await self.project.components.memory.aconfigure(memory_settings({"processing": memory_processing({
            "priority": 100, "recall": False, "extract": False, "summarize": True, "summary_format": "work_state",
            "completion": {"model": "test/summary"}, "goal_ids": ["work"], "keep_turns": 1,
            "summary_after_chars": 1})}))
        second = await (await self.session.run.submit("continue", engine="loop")).wait()
        self.assertEqual(str(second.data.status), "completed", second.data.error)
        self.assertIsNotNone(await self.project.components.memory.asummary(self.session.id))
        bad = ScriptedCompletion([chunk(calls=[call('{"identifier":"missing"}', name="skill_read")], finish="tool_calls")])
        self.app.engines.register("bad", LoopEngine(completion_fn=bad, completion_kwargs={"model": "test/main"}))
        third = await (await self.session.run.submit("invalid lookup", engine="bad")).wait()
        self.assertEqual(str(third.data.status), "failed")
        failed_steps = [step for step in await third.steps.alist() if step.kind == "tool"]
        target = {"component": "skills", "identifier": "guide"}
        snapshot = await self.refine.atarget_snapshot(target)
        arguments = {"target": target, "operation": "update", "expected_version": snapshot["version"],
            "reason": "Resolve identifiers with skill_list before reading", "patch": {"instructions": "List skills, then read an existing identifier."},
            "evidence": [{"session_id": self.session.id, "run_id": third.id, "step_id": failed_steps[0].id}]}
        proposer = ScriptedCompletion([chunk(calls=[call(json.dumps(arguments), name="refinement_propose")], finish="tool_calls")],
                                     [chunk("Proposal ready for review", finish="stop")])
        self.app.engines.register("propose", LoopEngine(completion_fn=proposer, completion_kwargs={"model": "test/refiner"}))
        analysis = await (await self.session.run.submit("refine observed failure", engine="propose")).wait()
        self.assertEqual(str(analysis.data.status), "completed", analysis.data.error)
        records = await self.refine.alist()
        identifier, proposal = next(iter(records.items()))
        self.assertEqual(proposal["source"]["run_id"], analysis.id)
        self.assertTrue(proposal["source"]["step_id"])
        self.assertEqual(await self.refine.atarget_snapshot(target), snapshot)
        approved = await self.approve(identifier)
        await self.refine.aapply(identifier, expected_version=approved["version"])
        followup = ScriptedCompletion(read_call, [chunk("followup", finish="stop")])
        self.app.engines.register("followup", LoopEngine(completion_fn=followup, completion_kwargs={"model": "test/main"}))
        fourth = await (await self.session.run.submit("use updated guide", engine="followup")).wait()
        self.assertEqual(str(fourth.data.status), "completed", fourth.data.error)
        tool_result = next(m for m in followup.requests[-1]["messages"] if m["role"] == "tool")
        self.assertIn("List skills", tool_result["content"])
        self.assertEqual(await first.steps.alist(), original_steps)
        self.assertEqual(await first.aresult(), original_result)
        self.assertEqual((await self.session.aconversation())[:len(raw_conversation)], raw_conversation)
        self.assertEqual((await goals.aload("work"))["run_refs"][0]["run_id"], first.id)
