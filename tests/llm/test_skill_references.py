"""Skill에서 Data/Tool을 참조하되 권한은 늘리지 않는지 검증한다."""

from tests.llm.support.skills import skill_markdown

from llm.providers.litellm import LiteLLMCompletionAdapter

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.skills import SkillComponent
from llm.components.data import DataComponent
from llm.components.tools import ToolComponent
from llm.core.agents import AgentDefinition
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, chunk, call
import json


SOURCE = '''from llm.components.tools import tool, ToolContract, ToolEffect, ToolIsolation
@tool(ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=False, retry_fields=()))
async def main():
    """Example."""
    return "ok"
'''


class SkillReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parts = [SkillComponent(), DataComponent(), ToolComponent()]
        self.app = LargeLanguageModel(self.root, components=self.parts)
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=[part.name for part in self.parts])
        self.skills, self.data, self.tools = (self.project.components[name] for name in ("skills", "data", "tools"))
        await self.data.acreate({"title": "Manual"}, identifier="manual")
        before = await self.data.asnapshot("manual")
        await self.data.awrite_file("manual", "reference.md", "Exact evidence", expected_revision=before["version"])
        await self.tools.acreate({"source": SOURCE}, identifier="helper")
        self.refs = [
            {"component": "data", "identifier": "manual", "path": "reference.md", "revision": (await self.data.asnapshot("manual"))["version"]},
            {"component": "tools", "identifier": "helper", "revision": (await self.tools.asnapshot("helper"))["version"]},
        ]
        await self.skills.acreate({"markdown": skill_markdown("guide", "Use evidence"), "resources": self.refs}, identifier="guide")

    async def test_read_validates_refs_without_activating_or_importing_tool(self):
        version = (await self.skills.asnapshot("guide"))["version"]
        with patch("llm.components.tools.component.load_tool", side_effect=AssertionError("must not execute source")):
            self.assertEqual((await self.skills.aread_resource("guide", 0, expected_revision=version))["text"], "Exact evidence")
            self.assertEqual((await self.skills.aread_resource("guide", 1, expected_revision=version))["reference"], self.refs[1])
            await self.skills.avalidate_references("guide")
        self.assertEqual(await self.tools.aenabled(), [])

    async def test_reference_change_is_explicit_and_stale_target_is_rejected(self):
        skill = await self.skills.asnapshot("guide")
        updated = await self.data.awrite_file("manual", "reference.md", "New", expected_revision=self.refs[0]["revision"])
        self.assertEqual(await self.skills.asnapshot("guide"), skill)
        with self.assertRaisesRegex(ValueError, "reference revision"):
            await self.skills.aread_resource("guide", 0, expected_revision=skill["version"])
        self.refs[0]["revision"] = updated["version"]
        await self.skills.aupdate("guide", {"resources": self.refs}, expected_version=skill["version"])
        current = await self.skills.asnapshot("guide")
        self.assertNotEqual(skill["version"], current["version"])
        self.assertEqual((await self.skills.aread_resource("guide", 0, expected_revision=current["version"]))["text"], "New")

    async def test_deletion_and_component_removal_are_protected(self):
        for target, identifier in ((self.data, "manual"), (self.tools, "helper")):
            with self.assertRaisesRegex(ValueError, "referenced"):
                await target.adelete(identifier)
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.data.adelete_file("manual", "reference.md", expected_revision=self.refs[0]["revision"])
        for name in ("data", "tools"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "referenced"):
                await self.project.components.aremove(name, permanent=True)
        await self.skills.aupdate("guide", {"resources": []})
        await self.data.adelete("manual")
        await self.tools.adelete("helper")

    async def test_disabled_skill_still_protects_stored_references(self):
        await self.project.components.aremove("skills")
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.tools.adelete("helper")
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.project.components.aremove("data", permanent=True)

    async def test_independent_agent_remains_visible_in_dependencies_after_reopen(self):
        agents = self.project.agents
        await agents.acreate({"purpose": "Read guide", "engine": "loop",
                              "resources": {"skills": ["guide"]}}, identifier="reader")
        await agents.acreate({"purpose": "Unrelated", "engine": "loop"}, identifier="other")
        snapshot = await agents.asnapshot("reader")
        expected = [{"agent_id": "reader", "revision": snapshot["revision"]}]
        self.assertEqual(await self.skills.aimpact("guide"), expected)
        await self.project.components.aselect([part.name for part in self.parts])
        self.assertEqual(await self.skills.adependencies("guide"), {"agents": expected, "children": []})
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.skills.adelete("guide")
        self.assertNotIn("agents", self.project.data.components)

        await self.app.shutdown()
        async with LargeLanguageModel(self.root, components=self.parts) as app:
            project = await app.projects.aload(self.project.id)
            skills = project.components.skills
            self.assertEqual(await skills.aimpact("guide"), expected)
            self.assertEqual(await skills.adependencies("guide"), {"agents": expected, "children": []})
            self.assertNotIn("agents", project.data.components)
            with self.assertRaisesRegex(ValueError, "referenced"):
                await skills.adelete("guide")
            await project.components.aselect([part.name for part in self.parts])
            self.assertEqual(await project.agents.asnapshot("reader"), snapshot)
            await project.agents.aupdate("reader", {"resources": {"skills": []}}, expected_revision=snapshot["revision"])
            self.assertEqual(await skills.adependencies("guide"), {"agents": [], "children": []})
            await skills.adelete("guide")

    async def test_clone_and_reopen_preserve_reference_versions(self):
        clone = await self.project.aclone()
        await clone.components.skills.avalidate_references("guide")
        version = (await clone.components.skills.asnapshot("guide"))["version"]
        self.assertEqual((await clone.components.skills.aread_resource("guide", 0, expected_revision=version))["text"], "Exact evidence")
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, components=self.parts) as app:
            reopened = await app.projects.aload(self.project.id)
            await reopened.components.skills.avalidate_references("guide")
            with self.assertRaisesRegex(ValueError, "referenced"):
                await reopened.components.tools.adelete("helper")

    async def test_missing_and_forged_reference_fields_are_rejected(self):
        for ref in ({**self.refs[0], "revision": "0" * 64}, {**self.refs[0], "path": "../x"},
                    {**self.refs[1], "identifier": "missing"}, {**self.refs[1], "authorize": True},
                    {"component": "data", "identifier": "manual"}):
            with self.subTest(ref=ref), self.assertRaises((ValueError, FileNotFoundError)):
                await self.skills.aupdate("guide", {"resources": [ref]})
        self.assertEqual((await self.skills.aload("guide"))["resources"], self.refs)

    async def test_loop_reads_resource_and_persists_step_without_tool_authority(self):
        model = ScriptedCompletion([chunk(calls=[call(json.dumps({"identifier": "guide", "index": 0}), name="skill_read_resource")], finish="tool_calls")],
                                   [chunk("Answer", finish="stop")])
        engine = LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))
        self.app.engines.register("read", engine)
        await self.project.agents.acreate({"engine": "read", "completion": {"model": "test/model"}, "tools": ["skill_read_resource"], "resources": {"skills": ["guide"]}}, identifier="read")
        session = await self.project.sessions.acreate(agent_ids=sorted(await self.project.agents.alist()))
        result = (await (await session.run.submit("Read guide", recipients=["read"])).wait())[0]
        self.assertEqual(str(result.data.status), "completed", result.data.error)
        self.assertNotIn("helper", [tool["function"]["name"] for tool in model.requests[0]["tools"]])
        self.assertIn("Exact evidence", json.dumps([m for m in model.requests[-1]["messages"] if m["role"] == "tool"]))
        steps = await result.steps.alist()
        self.assertTrue(any(step.kind == "tool" for step in steps))

    async def test_tool_source_change_invalidates_reference(self):
        await self.tools.aupdate("helper", {"source": SOURCE.replace('"ok"', '"changed"')})
        with self.assertRaisesRegex(ValueError, "reference revision"):
            await self.skills.avalidate_references("guide")
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.tools.adelete("helper")

    async def test_documented_example_and_backup_restore(self):
        from examples.llm.skill_data import create_example
        project, reference = await create_example(self.app)
        self.assertIn("explicit input", reference["text"])
        self.assertEqual(await project.components.tools.aenabled(), [])
        await project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "restore", components=self.parts) as app:
            restored = await app.projects.arestore_backup(self.root / "backup")
            await restored.components.skills.avalidate_references("guide")
            self.assertEqual(await restored.components.skills.asnapshot("guide"),
                             await project.components.skills.asnapshot("guide"))
            with self.assertRaisesRegex(ValueError, "referenced"):
                await restored.components["data"].adelete("manual")

    async def test_changed_data_prevents_graph_resume_and_preserves_source_run(self):
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.tool import ToolNode
        from llm.core.models import RunStatus
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.components.aselect([part.name for part in self.parts] + ["workflows"])
        self.app.engines.register("graph", GraphEngine(handlers={"tool": ToolNode()}))
        graph = (WorkflowGraph(entry="read")
                 .node("read", "tool", tool="skill_read_resource", arguments={"identifier": "guide", "index": 0}, pause_before=True)
                 .node("end", "end").connect("read", "end").to_dict())
        await self.project.components.workflows.acreate(graph, identifier="read")
        await self.project.agents.acreate({"engine": "graph", "engine_options": {"workflow": "read"}}, identifier="graph")
        session = await self.project.sessions.acreate(agent_ids=sorted(await self.project.agents.alist()))
        paused = (await (await session.run.submit("Read", recipients=["graph"])).wait())[0]
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        changed = await self.data.awrite_file("manual", "reference.md", "Changed", expected_revision=self.refs[0]["revision"])
        with self.assertRaisesRegex(ValueError, "reference revision"):
            await session.run.resume(paused.id, agent_id="graph")
        self.assertEqual((await paused.aget_data()).status, RunStatus.PAUSED)
        await self.data.awrite_file("manual", "reference.md", "Exact evidence", expected_revision=changed["version"])
        resumed = (await (await session.run.resume(paused.id, agent_id="graph")).wait())[0]
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
