"""Explicit Session participation and retained Agent reference integrity."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.core.models import Session
from llm.services.infrastructure.storage import record


class SessionAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = LargeLanguageModel(self.root / "workspace")
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate()
        for identifier in ("reader", "writer"):
            await self.project.agents.acreate({"engine": "loop"}, identifier=identifier)

    def bytes(self, session):
        return (session.paths.root / "session.json").read_bytes()

    async def test_creation_is_empty_until_application_explicitly_selects_agents(self):
        session = await self.project.sessions.acreate()
        self.assertEqual(session.data.agent_ids, [])
        chosen = ["reader", "writer"]
        explicit = await self.project.sessions.acreate(agent_ids=chosen)
        chosen.clear()
        self.assertEqual(explicit.data.agent_ids, ["reader", "writer"])
        self.assertEqual((await self.project.sessions.aload(explicit.id)).data.agent_ids, ["reader", "writer"])
        for invalid in (None, "all", ["unknown"], ["reader", "reader"], ["writer", "reader"], [True]):
            with self.subTest(value=invalid), self.assertRaises((ValueError, FileNotFoundError)):
                await self.project.sessions.acreate(agent_ids=invalid)
        self.assertEqual(len(await self.project.sessions.alist()), 2)

    async def test_required_stored_membership_cannot_be_defaulted_or_normalized(self):
        session = await self.project.sessions.acreate(agent_ids=["reader"])
        original = record(session.data)
        invalid = [{k: v for k, v in original.items() if k != "agent_ids"}]
        invalid += [{**original, "agent_ids": value}
                    for value in (None, {}, "all", ["reader", "reader"], ["writer", "reader"], [False])]
        for value in invalid:
            with self.subTest(value=value):
                before = deepcopy(value)
                with self.assertRaises(ValueError):
                    Session.from_record(value, session.paths)
                self.assertEqual(value, before)

    async def test_edit_is_explicit_and_invalid_edit_preserves_original_bytes(self):
        session = await self.project.sessions.acreate(agent_ids=["reader"])
        await session.asave(title="renamed")
        self.assertEqual(session.data.agent_ids, ["reader"])
        before = self.bytes(session)
        for invalid in (None, ["missing"], ["writer", "reader"], ["reader", "reader"]):
            with self.subTest(value=invalid), self.assertRaises((ValueError, FileNotFoundError)):
                await session.asave(title="must not commit", agent_ids=invalid)
            self.assertEqual(self.bytes(session), before)
        await session.asave(agent_ids=["writer"])
        self.assertEqual(session.data.agent_ids, ["writer"])
        await session.asave(agent_ids=[])
        self.assertEqual(session.data.agent_ids, [])

    async def test_soft_deleted_session_still_protects_participating_agent(self):
        session = await self.project.sessions.acreate(agent_ids=["reader"])
        revision = (await self.project.agents.asnapshot("reader"))["revision"]
        path = self.project.paths.agents / "records/reader.json"
        original = path.read_bytes()
        await session.adelete()
        with self.assertRaisesRegex(ValueError, "Session participation"):
            await self.project.agents.adelete("reader", expected_revision=revision)
        self.assertEqual(path.read_bytes(), original)
        await session.arestore()
        await session.asave(agent_ids=[])
        await self.project.agents.adelete("reader", expected_revision=revision)
        self.assertNotIn("reader", await self.project.agents.alist())

    async def test_clone_and_portable_backup_preserve_membership(self):
        session = await self.project.sessions.acreate(agent_ids=["reader", "writer"])
        clone = await session.aclone()
        self.assertEqual(clone.data.agent_ids, session.data.agent_ids)
        project_clone = await self.project.aclone()
        self.assertEqual([item.data.agent_ids for item in await project_clone.sessions.alist()],
                         [["reader", "writer"], ["reader", "writer"]])
        archive = await self.project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "restored") as other:
            restored = await other.projects.arestore_backup(archive)
            self.assertEqual([item.data.agent_ids for item in await restored.sessions.alist()],
                             [["reader", "writer"], ["reader", "writer"]])

    async def test_unknown_stored_member_blocks_read_clone_backup_and_agent_delete(self):
        session = await self.project.sessions.acreate(agent_ids=["reader"])
        path = session.paths.root / "session.json"
        value = json.loads(path.read_bytes())
        value["agent_ids"] = ["missing"]
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        revision = (await self.project.agents.asnapshot("reader"))["revision"]
        for operation in (lambda: self.project.sessions.aload(session.id), self.project.sessions.alist,
                          session.aclone, self.project.aclone,
                          lambda: self.project.abackup(self.root / "invalid-backup"),
                          lambda: self.project.agents.adelete("reader", expected_revision=revision)):
            with self.subTest(operation=operation), self.assertRaises((ValueError, FileNotFoundError)):
                await operation()
            self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / "invalid-backup").exists())
        # The portable archive validator must enforce the reference independently
        # of live SessionManager.load/list preflight and archive checksums.
        with self.assertRaisesRegex(ValueError, "Backup Session references"):
            self.app.project_manager._validate_archive(self.project.paths.root)
        self.assertEqual(path.read_bytes(), before)

    async def test_failed_membership_write_rolls_back_real_file(self):
        session = await self.project.sessions.acreate(agent_ids=["reader"])
        before = self.bytes(session)
        from llm.services.lifecycle import sessions
        original = sessions.atomic_domain_json
        reached = []
        def write_then_fail(path, value):
            original(path, value)
            reached.append(Path(path).read_bytes())
            raise OSError("after real membership replacement")
        with patch.object(sessions, "atomic_domain_json", write_then_fail):
            with self.assertRaisesRegex(OSError, "after real membership"):
                await session.asave(agent_ids=["writer"])
        self.assertEqual(len(reached), 1)
        self.assertEqual(json.loads(reached[0])["agent_ids"], ["writer"])
        self.assertEqual(self.bytes(session), before)
        self.assertEqual(session.data.agent_ids, ["reader"])

    async def test_attached_runtime_prevents_membership_edit(self):
        session = await self.project.sessions.acreate(agent_ids=["reader"])
        await session.run.start()
        before = self.bytes(session)
        with self.assertRaises(ValueError):
            await session.asave(agent_ids=["writer"])
        self.assertEqual(self.bytes(session), before)
        await session.run.shutdown()
        await session.asave(agent_ids=["writer"])
        self.assertEqual(session.data.agent_ids, ["writer"])
