"""기본 Project의 명시적 생성/재사용과 Component 설정 통합 조회를 검증한다."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.definitions import DefinitionComponent
from llm.components.tools import ToolComponent
from llm.core.models import ProjectConfig
from llm.services.infrastructure.storage import read_json


class Notes(DefinitionComponent):
    name = "notes"
    directory = "custom_notes"



class DefaultProjectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.app = LargeLanguageModel(self.root)
        self.addAsyncCleanup(self.app.shutdown)

    async def test_explicit_creation_selects_all_and_persists_file_storage(self):
        self.assertFalse(self.root.exists())
        project = await self.app.projects.aget_default(config=ProjectConfig(completion={"model": "test/model"}))
        data = await project.aget_data()
        self.assertEqual(data.conversation_storage, "file")
        self.assertNotIn("default_engine", data.config)
        self.assertEqual(data.config.completion["model"], "test/model")
        self.assertEqual(set(data.components), {"tools", "skills", "mcp", "rag", "agents", "workflows", "memory", "prompts"})
        for name in data.components:
            self.assertTrue((project.paths.root / name if name == "tools" else project.paths.root / name / "records").is_dir())
            self.assertFalse((project.paths.root / name / "component.json").exists())
        tools = await project.components.aget("tools")
        self.assertEqual(await tools.aenabled(), [])
        session = await project.sessions.acreate()
        with self.assertRaises(TypeError):
            await session.run.submit("engine is still explicit")
        self.assertEqual(await session.aconversation(), [])
        self.assertEqual(await session.run.alist(), [])

    async def test_repeated_and_concurrent_requests_reuse_without_overwriting(self):
        first, second = await asyncio.gather(self.app.projects.aget_default(), self.app.projects.aget_default())
        self.assertEqual(first.id, second.id)
        await first.asave(title="Customized", config=ProjectConfig(default_engine="other", custom={"theme": "dark"}))
        await first.components.aselect(["tools"])
        tools = await first.components.aget("tools")
        await tools.aconfigure({"enabled": [], "ui": {"label": "도구"}})
        again = await self.app.projects.aget_default(title="Ignored", config=ProjectConfig(custom={"theme": "light"}))
        data = await again.aget_data()
        self.assertEqual(data.title, "Customized")
        self.assertEqual(data.config["default_engine"], "other")
        self.assertEqual(data.config["custom"]["theme"], "dark")
        self.assertEqual(data.components, ("tools",))
        self.assertEqual((await again.aconfiguration())["components"]["tools"]["configuration"]["ui"]["label"], "도구")
        self.assertEqual(len(await self.app.projects.alist()), 1)

    async def test_reopen_and_clone_preserve_default_identity(self):
        project = await self.app.projects.aget_default()
        clone = await project.aclone(title="Copy")
        self.assertNotEqual(project.id, clone.id)
        await self.app.shutdown()
        async with LargeLanguageModel(self.root) as app:
            reopened = await app.projects.aget_default()
            self.assertEqual(reopened.id, project.id)
            self.assertEqual(len(await app.projects.alist()), 2)

    async def test_deleted_project_requires_restore_permanent_deletion_creates_new_id(self):
        project = await self.app.projects.aget_default()
        await project.adelete()
        with self.assertRaisesRegex(ValueError, "deleted"):
            await self.app.projects.aget_default()
        await project.arestore()
        self.assertEqual((await self.app.projects.aget_default()).id, project.id)
        await project.adelete(permanent=True)
        replacement = await self.app.projects.aget_default()
        self.assertNotEqual(replacement.id, project.id)

    async def test_memory_backend_uses_file_only_for_new_default_project(self):
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, conversation_storage="memory") as app:
            default = await app.projects.aget_default()
            regular = await app.projects.acreate("Regular")
            self.assertEqual((await default.aget_data()).conversation_storage, "file")
            self.assertEqual((await regular.aget_data()).conversation_storage, "memory")
            await default.asave(conversation_storage="memory")
            self.assertEqual((await (await app.projects.aget_default()).aget_data()).conversation_storage, "memory")

    async def test_custom_registration_and_aggregate_detached_configuration(self):
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, components=[ToolComponent(), Notes()]) as app:
            project = await app.projects.aget_default(config={"component_configurations": {"notes": {"language": "ko", "future": {"limit": 3}}}})
            view = await project.aconfiguration()
            self.assertEqual(set(view["components"]), {"tools", "notes"})
            self.assertEqual(view["components"]["notes"]["directory"], "custom_notes")
            view["components"]["notes"]["configuration"]["future"]["limit"] = 9
            self.assertEqual((await project.aconfiguration())["components"]["notes"]["configuration"]["future"]["limit"], 3)
            handle = await project.components.aget("notes")
            await handle.aconfigure(view["components"]["notes"]["configuration"])
            self.assertEqual((await project.aconfiguration())["components"]["notes"]["configuration"]["future"]["limit"], 9)
            await project.components.aremove("notes")
            self.assertNotIn("notes", (await project.aconfiguration())["components"])
            await project.adelete()
            with self.assertRaisesRegex(ValueError, "deleted"):
                await project.aconfiguration()

    async def test_missing_engine_and_custom_storage_do_not_create_a_project(self):
        from llm.services.configuration import ServiceConfig
        from llm.services.history.conversation import MemoryConversations
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, engines={}) as app:
            with self.assertRaisesRegex(ValueError, "loop"):
                app.projects.get_default()
            self.assertFalse(self.root.exists())
        async with LargeLanguageModel(self.root, services=ServiceConfig(conversations=MemoryConversations())) as app:
            with self.assertRaisesRegex(ValueError, "custom conversation"):
                await app.projects.aget_default()
            self.assertEqual(await app.projects.alist(), [])
            self.assertFalse((self.root / "projects" / "default-project.json").exists())

    async def test_failed_creation_rolls_back_default_pointer(self):
        manager = self.app.project_manager
        with patch.object(manager, "_create", side_effect=OSError("interrupted before create")):
            with self.assertRaises(OSError):
                await self.app.projects.aget_default()
        self.assertFalse((self.root / "projects" / "default-project.json").exists())
        self.assertEqual(await self.app.projects.alist(), [])
        project = await self.app.projects.aget_default()
        pointer = read_json(self.root / "projects" / "default-project.json")
        self.assertEqual(project.id, pointer["project_id"])
        self.assertFalse(pointer["pending"])

    async def test_pointer_completion_failure_rolls_back_project_and_pointer(self):
        from llm.services.infrastructure.storage import atomic_json
        def fail_completion(path, data):
            if path.name == "default-project.json" and not data["pending"]:
                raise OSError("pointer completion failed")
            atomic_json(path, data)
        with patch("llm.services.lifecycle.projects.atomic_json", side_effect=fail_completion):
            with self.assertRaises(OSError):
                await self.app.projects.aget_default()
        existing = await self.app.projects.alist()
        self.assertEqual(len(existing), 0)
        self.assertFalse((self.root / "projects" / "default-project.json").exists())
        resumed = await self.app.projects.aget_default()
        self.assertEqual([p.id for p in await self.app.projects.alist()], [resumed.id])

    async def test_shutdown_rejects_default_and_configuration_calls(self):
        project = await self.app.projects.aget_default()
        await self.app.shutdown()
        with self.assertRaises(RuntimeError):
            await self.app.projects.aget_default()
        with self.assertRaises(RuntimeError):
            await project.aconfiguration()
