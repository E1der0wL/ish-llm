"""Skill 패키지의 자료·revision·수명 관리. 실제 Linux 파일과 기존 서비스를 사용한다."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.skills import SkillComponent
from llm.components.agents import AgentComponent
from llm.services.infrastructure.storage import revision_token


class SkillPackageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.component = SkillComponent()
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component, AgentComponent()])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=["skills", "agents"])
        self.skills = self.project.components.skills
        await self.skills.acreate({"instructions": "Check the reference."}, identifier="guide")

    async def put(self, path="references/manual.md", content="Original reference"):
        version = (await self.skills.asnapshot("guide"))["version"]
        return await self.skills.awrite_file("guide", path, content, expected_revision=version)

    async def test_layout_revision_and_stale_edit(self):
        before = await self.skills.asnapshot("guide")
        after = await self.put()
        self.assertTrue((self.project.paths.root / "skills/guide/skill.json").is_file())
        self.assertFalse((self.project.paths.root / "skills/records").exists())
        self.assertNotEqual(before["version"], after["version"])
        self.assertEqual(after["version"], revision_token(after["data"]))
        with self.assertRaisesRegex(ValueError, "edit_conflict"):
            await self.skills.aupdate("guide", {"instructions": "stale"}, expected_version=before["version"])
        with self.assertRaisesRegex(ValueError, "edit_conflict"):
            await self.skills.awrite_file("guide", "other", "stale", expected_revision=before["version"])
        with self.assertRaisesRegex(ValueError, "manifests"):
            await self.skills.aupdate("guide", {"files": {}})

    async def test_resource_read_and_delete_protection(self):
        after = await self.put()
        await self.skills.aupdate("guide", {"resources": [{"path": "references/manual.md"}]}, expected_version=after["version"])
        snapshot = await self.skills.asnapshot("guide")
        result = await self.skills.aread_resource("guide", 0, expected_revision=snapshot["version"])
        self.assertEqual(result["text"], "Original reference")
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.skills.adelete_file("guide", "references/manual.md", expected_revision=snapshot["version"])
        await self.skills.aupdate("guide", {"resources": []})
        snapshot = await self.skills.asnapshot("guide")
        await self.skills.adelete_file("guide", "references/manual.md", expected_revision=snapshot["version"])
        self.assertEqual(await self.skills.alist_files("guide"), {})

    async def test_clone_backup_restore_and_binary_ranges(self):
        await self.put("examples/sample.bin", b"\x00\xffabc")
        clone = await self.project.aclone()
        self.assertEqual(await clone.components.skills.aread_file("guide", "examples/sample.bin", offset=1, length=2), b"\xffa")
        await self.project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "restored", components=[SkillComponent(), AgentComponent()]) as other:
            restored = await other.projects.arestore_backup(self.root / "backup")
            self.assertEqual(await restored.components.skills.asnapshot("guide"), await self.skills.asnapshot("guide"))
            self.assertEqual(await restored.components.skills.aread_file("guide", "examples/sample.bin"), b"\x00\xffabc")

    async def test_fork_copies_files_and_preserves_lineage_protection(self):
        before = await self.put()
        await self.skills.afork("guide", {**before["data"], "instructions": "Fork"}, identifier="child", expected_revision=before["version"])
        self.assertEqual(await self.skills.aread_file("child", "references/manual.md"), b"Original reference")
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.skills.adelete("guide")
        await self.skills.adelete("child")
        await self.project.components.agents.acreate({"purpose": "Use guide", "engine": "loop", "resources": {"skills": ["guide"]}}, identifier="reviewer")
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.skills.adelete("guide")
        await self.project.components.agents.adelete("reviewer")
        await self.skills.adelete("guide")
        self.assertFalse((self.project.paths.root / "skills/guide").exists())

    async def test_paths_links_tampering_and_legacy_rejected(self):
        for path in ("../escape", "/tmp/file", "a//b", "a/./b", "C:/x", "a\\b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.put(path)
        await self.put()
        file = self.project.paths.root / "skills/guide/files/references/manual.md"
        file.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "integrity"):
            await self.skills.aload("guide")
        file.unlink()
        file.symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "linked"):
            await self.skills.aload("guide")
        file.unlink()
        (self.project.paths.root / "skills/records").mkdir()
        with self.assertRaisesRegex(ValueError, "Legacy"):
            await self.skills.alist()

    async def test_write_manifest_failure_rolls_back_bytes_and_revision(self):
        original = await self.put()
        with patch.object(self.component, "_write", side_effect=OSError("manifest failure")):
            with self.assertRaises(OSError):
                await self.put(content="changed")
        self.assertEqual(await self.skills.asnapshot("guide"), original)
        self.assertEqual(await self.skills.aread_file("guide", "references/manual.md"), b"Original reference")

    async def test_runtime_resource_binding_rejects_changed_file(self):
        await self.put()
        await self.skills.aupdate("guide", {"resources": [{"path": "references/manual.md"}]})
        tools = self.component.resolve_runtime(self.project.data, "tools", data_factory=lambda _: self.skills)
        result = await tools.get("skill_read_resource").handler({"identifier": "guide", "index": 0})
        self.assertEqual(result["text"], "Original reference")
        await self.put(content="changed")
        with self.assertRaisesRegex(ValueError, "edit_conflict"):
            await tools.get("skill_read_resource").handler({"identifier": "guide", "index": 0})
        changed = self.component.resolve_runtime(self.project.data, "tools", data_factory=lambda _: self.skills)
        self.assertNotEqual(tools.contracts(), changed.contracts())
