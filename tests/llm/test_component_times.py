"""Managed times share semantics while storage and native history keep their owners."""

import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components.data import DataComponent
from llm.components.skills import SkillComponent
from llm.components.prompts import PromptComponent
from llm.components.tools import ToolComponent
from llm.components.memory import MemoryComponent
from llm.core.record_times import RecordTimes
from llm.llm import LargeLanguageModel
from tests.llm.support.skills import skill_markdown


FIRST = RecordTimes("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")
CHANGED = RecordTimes(FIRST.created_at, "2026-01-02T00:00:00+00:00")


class TimeValueTests(unittest.TestCase):
    def test_explicit_utc_only_and_closed_fields(self):
        self.assertEqual(RecordTimes.read(FIRST.to_dict()), FIRST)
        invalid = [{}, {"created_at": FIRST.created_at}, {**FIRST.to_dict(), "extra": True},
                   {**FIRST.to_dict(), "updated_at": None},
                   {**FIRST.to_dict(), "updated_at": "2026-01-01T00:00:00"},
                   {**FIRST.to_dict(), "updated_at": "2026-01-01T00:00:00+09:00"}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                RecordTimes.read(value)


class ComponentTimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.owners = [DataComponent(), SkillComponent(), PromptComponent(), ToolComponent(), MemoryComponent()]
        self.app = LargeLanguageModel(self.root / "workspace", components=self.owners)
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=[owner.name for owner in self.owners])

    async def test_definitions_and_packages_share_times_without_changing_payload(self):
        definitions = {
            "data": ({"title": "one"}, {"title": "two"}),
            "skills": ({"markdown": skill_markdown("item", "One.")},
                       {"markdown": skill_markdown("item", "Two.")}),
            "prompts": ({"messages": [{"role": "system", "content": "one"}]},
                        {"messages": [{"role": "system", "content": "two"}]}),
            "tools": ({"source": 'from llm import tool\n@tool()\nasync def main():\n    """One."""\n    return 1\n'},
                      {"source": 'from llm import tool\n@tool()\nasync def main():\n    """Two."""\n    return 2\n'}),
        }
        for name, (initial, changed) in definitions.items():
            with self.subTest(component=name):
                data = self.project.components[name]
                with patch.object(RecordTimes, "create", return_value=FIRST):
                    await data.acreate(initial, identifier="item")
                before = await data.asnapshot("item")
                self.assertEqual(await data.aget_times("item"), FIRST.to_dict())
                self.assertNotIn("created_at", before["data"])
                with patch.object(RecordTimes, "touch", return_value=CHANGED) as touch:
                    await data.asave("item", initial)
                    self.assertEqual(await data.asnapshot("item"), before)
                    self.assertEqual(await data.aget_times("item"), FIRST.to_dict())
                    touch.assert_not_called()
                    await data.asave("item", changed)
                self.assertEqual(await data.aget_times("item"), CHANGED.to_dict())
                self.assertNotEqual((await data.asnapshot("item"))["version"], before["version"])
                detached = await data.aget_times("item")
                detached["created_at"] = "not authoritative"
                self.assertEqual(await data.aget_times("item"), CHANGED.to_dict())

    async def test_file_change_noop_delete_and_rollback(self):
        data = self.project.components["data"]
        with patch.object(RecordTimes, "create", return_value=FIRST):
            await data.acreate({"title": "Data"}, identifier="item")
        before = await data.asnapshot("item")
        with patch.object(RecordTimes, "touch", return_value=CHANGED):
            after = await data.awrite_file("item", "x", b"value", expected_revision=before["version"])
        self.assertEqual(await data.aget_times("item"), CHANGED.to_dict())
        with patch.object(RecordTimes, "touch") as touch:
            await data.awrite_file("item", "x", b"value", expected_revision=after["version"])
            touch.assert_not_called()
        from llm.components import storage
        write = storage.atomic_json
        def failed_manifest(path, value):
            if Path(path).name == "records.manifest.json":
                raise OSError("test storage failure")
            return write(path, value)
        with patch.object(storage, "atomic_json", side_effect=failed_manifest):
            with self.assertRaises(OSError):
                await data.aupdate("item", {"title": "not committed"})
        self.assertEqual(await data.asnapshot("item"), after)
        self.assertEqual(await data.aget_times("item"), CHANGED.to_dict())
        await data.adelete("item")
        with self.assertRaises(FileNotFoundError):
            await data.aget_times("item")

    async def test_missing_times_fail_without_repairing_files(self):
        data = self.project.components["data"]
        await data.acreate({"title": "Data"}, identifier="item")
        path = self.project.paths.root / "data/records.manifest.json"
        value = json.loads(path.read_text())
        del value["times"]
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        with self.assertRaises(ValueError):
            await data.aload("item")
        self.assertEqual(path.read_bytes(), raw)

    async def test_native_package_times_are_required_without_backfill(self):
        skills, tools = self.project.components.skills, self.project.components.tools
        await skills.acreate({"markdown": skill_markdown("item", "Fixture")}, identifier="item")
        await tools.acreate({"source": "async def main():\n    return None\n"}, identifier="item")
        operational = self.project.paths.root / "skills/_state/records/item.json"
        catalog = self.project.paths.root / "skills/_state/manifest.json"
        value = json.loads(operational.read_text())
        del value["times"]
        raw = json.dumps(value).encode()
        operational.write_bytes(raw)
        inventory = json.loads(catalog.read_text())
        inventory["records"]["item"] = hashlib.sha256(raw).hexdigest()
        catalog.write_text(json.dumps(inventory))
        with self.assertRaisesRegex(ValueError, "times"):
            await skills.aload("item")
        self.assertEqual(operational.read_bytes(), raw)
        manifest = self.project.paths.root / "tools/packages.manifest.json"
        value = json.loads(manifest.read_text())
        del value["times"]
        raw = json.dumps(value).encode()
        manifest.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "times"):
            await tools.aload("item")
        self.assertEqual(manifest.read_bytes(), raw)

    async def test_json_boolean_and_integer_are_distinct_changes(self):
        data = self.project.components["data"]
        await data.acreate({"metadata": {"value": 1}}, identifier="item")
        before = await data.asnapshot("item")
        await data.aupdate("item", {"metadata": {"value": True}})
        after = await data.asnapshot("item")
        self.assertNotEqual(after["version"], before["version"])
        self.assertIs(after["data"]["metadata"]["value"], True)

    async def test_clone_is_new_registration_and_backup_preserves_times(self):
        data = self.project.components["data"]
        with patch.object(RecordTimes, "create", return_value=FIRST):
            await data.acreate({"title": "Data"}, identifier="item")
        other = RecordTimes(CHANGED.updated_at, CHANGED.updated_at)
        with patch.object(RecordTimes, "create", return_value=other):
            clone = await self.project.aclone()
        self.assertEqual(await clone.components["data"].aget_times("item"), other.to_dict())
        self.assertEqual(await data.aget_times("item"), FIRST.to_dict())
        await self.project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "restore", components=self.owners) as backend:
            restored = await backend.projects.arestore_backup(self.root / "backup")
            self.assertEqual(await restored.components["data"].aget_times("item"), FIRST.to_dict())

    async def test_memory_native_history_is_the_only_owner(self):
        data = self.project.components["memory"]
        await data.acreate({"content": "remember this"}, identifier="item")
        original = await data.aload("item")
        self.assertEqual(await data.aget_times("item"), RecordTimes.from_record(original).to_dict())
        manifest = json.loads((self.project.paths.root / "memory/records.manifest.json").read_text())
        self.assertNotIn("times", manifest)
        await data.aupdate("item", {"content": "remember this"},
            expected_revision=original["revision"], expected_generation=original["generation"])
        self.assertEqual(await data.aload("item"), original)
        self.assertEqual(len(await data.ahistory("item")), 1)
        await data.aupdate("item", {"content": "updated"},
            expected_revision=original["revision"], expected_generation=original["generation"])
        changed = await data.aload("item")
        self.assertEqual(changed["created_at"], original["created_at"])
        self.assertEqual(await data.aget_times("item"), RecordTimes.from_record(changed).to_dict())
        clone = await self.project.aclone()
        self.assertEqual(await clone.components["memory"].ahistory("item"), await data.ahistory("item"))
