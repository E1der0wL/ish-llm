"""프로젝트별 저장 선택과 재시작·복제·수명 주기 경계를 검증한다."""

import asyncio
from pathlib import Path
import tempfile
import unittest

from llm.llm import LargeLanguageModel
from llm.services.configuration import ServiceConfig
from llm.services.history.conversation import MemoryConversations
from llm.services.infrastructure.storage import atomic_json, read_json
from tests.llm.test_facade_requests import ControlledEngine


class ProjectConversationStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def backend(self, **kwargs):
        app = LargeLanguageModel(self.root, engines={"test": ControlledEngine()}, **kwargs)
        self.addAsyncCleanup(app.shutdown)
        return app

    async def answer(self, session, text):
        run = await (await session.run.submit(text, engine="test")).wait(timeout=15)
        self.assertEqual((await run.aresponse()).content, "answer:" + text)
        return run

    async def test_mixed_projects_execute_and_reopen_with_persisted_choices(self):
        app = self.backend()
        projects = [await app.projects.acreate(mode, conversation_storage=mode)
                    for mode in ("file", "memory")]
        sessions = [await project.sessions.acreate("chat") for project in projects]
        await asyncio.gather(*(self.answer(session, mode) for session, mode in zip(sessions, ("file", "memory"))))
        for project, session, mode in zip(projects, sessions, ("file", "memory")):
            self.assertEqual(read_json(project.paths.root / "project.json")["conversation_storage"], mode)
            self.assertEqual([m.content for m in await session.aconversation()], [mode, "answer:" + mode])
            self.assertEqual(session.paths.conversation.exists(), mode == "file")
        store = app.project_manager.sessions.conversations(sessions[1].data)
        await app.shutdown()
        self.assertEqual(store.list(), [])
        # 백엔드 기본값을 바꿔도 저장된 프로젝트 선택은 그대로다.
        reopened = self.backend(conversation_storage="memory")
        for project, session, mode in zip(projects, sessions, ("file", "memory")):
            loaded_project = await reopened.projects.aload(project.id)
            loaded_session = await loaded_project.sessions.aload(session.id)
            self.assertEqual((await loaded_project.aget_data()).conversation_storage, mode)
            self.assertEqual(len(await loaded_session.aconversation()), 2 if mode == "file" else 0)
            self.assertEqual(len(await loaded_session.run.alist()), 1)
            await self.answer(loaded_session, "new")
            self.assertEqual(len(await loaded_session.aconversation()), 4 if mode == "file" else 2)
        await reopened.shutdown()
        file_default = self.backend()
        memory_session = await (await file_default.projects.aload(projects[1].id)).sessions.aload(sessions[1].id)
        await self.answer(memory_session, "still memory")
        self.assertFalse(memory_session.paths.conversation.exists())

    async def test_defaults_are_pinned_and_empty_project_can_change_selection(self):
        app = self.backend(conversation_storage="memory")
        project = await app.projects.acreate("default")
        self.assertEqual(project.data.conversation_storage, "memory")
        await project.asave(conversation_storage="file", title="changed")
        self.assertEqual(project.data.conversation_storage, "file")
        # 오래된 ProjectHandle 내부 스냅샷도 새 Session의 저장 선택에 영향을 주지 않는다.
        session = await project.sessions.acreate()
        await self.answer(session, "file")
        self.assertTrue(session.paths.conversation.exists())
        await project.asave(title="renamed", config={"arbitrary": 1}, conversation_storage="file")
        with self.assertRaisesRegex(ValueError, "Sessions"):
            await project.asave(conversation_storage="memory")
        await session.run.shutdown()
        await session.adelete()
        with self.assertRaisesRegex(ValueError, "Sessions"):
            await project.asave(conversation_storage="memory")
        # 서비스 직접 호출에도 같은 경계를 적용한다.
        model = project.data
        model.conversation_storage = "memory"
        with self.assertRaisesRegex(ValueError, "Sessions"):
            app.project_manager.save(model)
        self.assertEqual(project.data.conversation_storage, "file")
        self.assertEqual(project.data.config["arbitrary"], 1)

    async def test_clone_preserves_project_mode_and_uses_destination_store(self):
        app = self.backend()
        memory = await app.projects.acreate("memory", conversation_storage="memory")
        session = await memory.sessions.acreate()
        await self.answer(session, "source")
        await session.run.shutdown()
        cloned = await memory.aclone()
        self.assertEqual(cloned.data.conversation_storage, "memory")
        cloned_session = (await cloned.sessions.alist())[0]
        self.assertEqual([m.content for m in await cloned_session.aconversation()], ["source", "answer:source"])
        self.assertFalse(cloned_session.paths.conversation.exists())
        file_project = await app.projects.acreate("file")
        manager = app.project_manager.sessions
        file_session = manager.clone(session.data, file_project.data)
        self.assertTrue(file_session.paths.conversation.exists())
        memory_session = manager.clone(file_session, memory.data)
        self.assertFalse(memory_session.paths.conversation.exists())
        self.assertEqual(len(manager.conversations(memory_session).list()), 2)
        await self.answer(cloned_session, "clone only")
        self.assertEqual(len(await session.aconversation()), 2)

    async def test_project_metadata_requires_explicit_storage_and_component_selection(self):
        app = self.backend()
        project = await app.projects.acreate("metadata")
        path = project.paths.root / "project.json"
        data = read_json(path)
        for field in ("conversation_storage", "components"):
            with self.subTest(field=field):
                incomplete = dict(data)
                incomplete.pop(field)
                atomic_json(path, incomplete)
                with self.assertRaisesRegex(ValueError, "requires components and conversation_storage"):
                    await app.projects.aload(project.id)
                self.assertEqual(read_json(path), incomplete)
        atomic_json(path, data)
        self.assertEqual((await app.projects.aload(project.id)).data.conversation_storage, "file")

    async def test_invalid_selection_and_custom_factory_conflicts_fail_before_creation(self):
        app = self.backend()
        for mode in ("invalid", False, {}, 1):
            with self.assertRaises(ValueError):
                await app.projects.acreate(conversation_storage=mode)
        self.assertEqual(await app.projects.alist(), [])
        project = await app.projects.acreate()
        path = project.paths.root / "project.json"
        data = read_json(path)
        data["conversation_storage"] = "invalid"
        atomic_json(path, data)
        with self.assertRaises(ValueError):
            await app.projects.aload(project.id)
        data["conversation_storage"] = "file"
        atomic_json(path, data)
        session = await project.sessions.acreate()
        await app.shutdown()
        custom = self.backend(services=ServiceConfig(conversations=MemoryConversations()))
        for mode in ("file", "memory"):
            with self.assertRaisesRegex(ValueError, "custom conversation factory"):
                await custom.projects.acreate(conversation_storage=mode)
        self.assertEqual(len(await custom.projects.alist()), 1)
        loaded = await (await custom.projects.aload(project.id)).sessions.aload(session.id)
        with self.assertRaisesRegex(ValueError, "custom conversation factory"):
            await loaded.aconversation()
        self.assertFalse(session.paths.conversation.exists())
        inherited = await custom.projects.acreate("custom")
        self.assertIsNone(inherited.data.conversation_storage)
        await self.answer(await inherited.sessions.acreate(), "custom")

    async def test_memory_project_cleanup_with_file_backend_default(self):
        app = self.backend()
        project = await app.projects.acreate(conversation_storage="memory")
        session = await project.sessions.acreate()
        await self.answer(session, "retained")
        store = app.project_manager.sessions.conversations(session.data)
        await session.run.shutdown()
        await project.adelete()
        await project.arestore()
        self.assertEqual(len(await session.aconversation()), 2)
        await project.adelete(permanent=True)
        self.assertEqual(store.list(), [])
        self.assertFalse(project.paths.root.exists())
