"""선택형 Data 파일 패키지의 CAS·복제·백업·재시작·원자성을 검증한다."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import asyncio
import threading

from llm.llm import LargeLanguageModel, DataComponent


class DataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.component = DataComponent()
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=["data"])
        self.data = self.project.components["data"]
        await self.data.acreate({"title": "Dataset", "metadata": {"temporary": True}}, identifier="samples")

    async def test_definition_schema_does_not_invent_field_descriptions(self):
        properties = self.component.schema["properties"]
        self.assertEqual(properties["title"], {"type": "string"})
        self.assertEqual(properties["description"], {"type": "string"})
        self.assertNotIn("description", await self.data.aload("samples"))

    async def test_binary_import_range_and_cas(self):
        source = self.root / "input.bin"
        content = bytes(range(256)) * 8192
        source.write_bytes(content)
        before = await self.data.asnapshot("samples")
        after = await self.data.aimport_file("samples", "train/input.bin", source, expected_revision=before["version"])
        self.assertEqual(after["data"]["files"]["train/input.bin"]["bytes"], len(content))
        self.assertEqual(await self.data.aread_file("samples", "train/input.bin", offset=7, length=23), content[7:30])
        with self.assertRaisesRegex(ValueError, "edit_conflict"):
            await self.data.awrite_file("samples", "train/input.bin", b"other", expected_revision=before["version"])
        self.assertEqual(await self.data.aread_file("samples", "train/input.bin"), content)

    async def test_clone_backup_reopen_and_delete(self):
        before = await self.data.asnapshot("samples")
        after = await self.data.awrite_file("samples", "tmp/result.json", "{}", expected_revision=before["version"])
        clone = await self.project.aclone()
        self.assertEqual(await clone.components["data"].asnapshot("samples"), after)
        await self.project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "other", components=[DataComponent()]) as other:
            restored = await other.projects.arestore_backup(self.root / "backup")
            self.assertEqual(await restored.components["data"].aread_file("samples", "tmp/result.json"), b"{}")
        await self.app.shutdown()
        async with LargeLanguageModel(self.root / "workspace", components=[DataComponent()]) as reopened:
            data = (await reopened.projects.aload(self.project.id)).components["data"]
            self.assertEqual(await data.asnapshot("samples"), after)
            await data.adelete("samples", expected_version=after["version"])
            self.assertFalse((self.project.paths.root / "data/samples").exists())

    async def test_optional_selection_and_closed_record(self):
        empty = await self.app.projects.acreate(components=[])
        self.assertFalse((empty.paths.root / "data").exists())
        self.assertEqual(await self.data.aget_config(), {})
        for definition in ({"instructions": "unexpected"}, {"files": {}}, {"policy": {}}):
            with self.subTest(definition=definition), self.assertRaises(ValueError):
                await self.data.acreate(definition)

    async def test_transaction_failure_keeps_prior_state(self):
        before = await self.data.asnapshot("samples")
        with patch.object(self.component, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.data.awrite_file("samples", "x", b"new", expected_revision=before["version"])
        self.assertEqual(await self.data.asnapshot("samples"), before)
        self.assertEqual(await self.data.alist_files("samples"), {})

    async def test_file_delete_requires_current_revision(self):
        before = await self.data.asnapshot("samples")
        after = await self.data.awrite_file("samples", "x", b"new", expected_revision=before["version"])
        with self.assertRaises(ValueError):
            await self.data.adelete_file("samples", "x", expected_revision=before["version"])
        await self.data.adelete_file("samples", "x", expected_revision=after["version"])
        self.assertEqual(await self.data.alist_files("samples"), {})

    async def test_external_edits_and_symlinks_fail_closed(self):
        before = await self.data.asnapshot("samples")
        await self.data.awrite_file("samples", "x", b"new", expected_revision=before["version"])
        path = self.project.paths.root / "data/samples/files/x"
        path.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "integrity"):
            await self.data.aread_file("samples", "x")
        path.unlink()
        path.symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "linked"):
            await self.data.aload("samples")

    async def test_unregistered_package_files_are_not_silently_omitted(self):
        stray = self.project.paths.root / "data/samples/stray.txt"
        stray.write_text("not registered")
        with self.assertRaisesRegex(ValueError, "Unregistered package entry"):
            await self.data.aload("samples")
        with self.assertRaisesRegex(ValueError, "Unregistered package entry"):
            await self.project.aclone()

    async def test_cancelled_write_drains_atomic_commit(self):
        before = await self.data.asnapshot("samples")
        started, finish = threading.Event(), threading.Event()
        digest = self.component._digest
        def paused(stream, output=None):
            if output is not None:
                started.set()
                if not finish.wait(5):
                    raise TimeoutError("test did not release writer")
            return digest(stream, output)
        with patch.object(self.component, "_digest", side_effect=paused):
            task = asyncio.create_task(self.data.awrite_file("samples", "x", b"complete", expected_revision=before["version"]))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                task.cancel()
            finally:
                finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await self.data.aread_file("samples", "x"), b"complete")
