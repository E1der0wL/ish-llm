"""라이브러리의 명시적 생성과 대상별 설정 계약. UI 초기 선택 정책은 hub에서 검증한다."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.core.models import ProjectConfig
from llm.components.definitions import DefinitionComponent
from llm.engines.loop import LoopEngine
from llm.services.infrastructure.storage import atomic_json, read_json
from tests.llm.test_loop import ScriptedCompletion, chunk


class Notes(DefinitionComponent):
    name = "notes"
    directory = "custom_notes"

    def configuration_schema(self):
        from llm.core.schema import implementation_schema, open_schema
        return implementation_schema(config=open_schema("test Notes implementation", category="implementation"))


class ProjectCreationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "workspace"
        self.app = LargeLanguageModel(self.root, components=[Notes()], engines={})
        self.addAsyncCleanup(self.app.shutdown)

    async def test_no_implicit_creation_selection_or_default_api(self):
        self.assertFalse(self.root.exists())
        for owner in (self.app.projects, self.app.project_manager):
            self.assertFalse(hasattr(owner, "get_default"))
            self.assertFalse(hasattr(owner, "aget_default"))
        self.assertEqual(await self.app.projects.alist(), [])
        project = await self.app.projects.acreate("Explicit")
        self.assertEqual((await project.aget_data()).components, ())
        self.assertEqual(await project.sessions.alist(), [])
        self.assertFalse((project.paths.root / "custom_notes").exists())
        self.assertEqual(ProjectConfig().parameters, {})
        self.assertEqual(ProjectConfig().policies, {})
        self.assertFalse(list(self.root.rglob("default-project.json")))

    async def test_create_never_reuses_an_existing_project(self):
        one, two = await asyncio.gather(self.app.projects.acreate("Same"), self.app.projects.acreate("Same"))
        self.assertNotEqual(one.id, two.id)
        clone = await one.aclone()
        self.assertNotEqual(clone.id, one.id)
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, components=[Notes()], engines={}) as app:
            self.assertEqual({p.id for p in await app.projects.alist()}, {one.id, two.id, clone.id})
            self.assertEqual((await app.projects.aload(one.id)).id, one.id)

    async def test_component_entry_points_share_config_and_cas(self):
        project = await self.app.projects.acreate(components=["notes"], config=ProjectConfig(
            parameters={"components": {"notes": {'config': {'language': 'ko', 'future': {'limit': 3}}}}}))
        view = await project.aconfiguration()
        self.assertEqual(view["values"]["config"], (await project.aget_data()).config)
        self.assertEqual(view["components"]["notes"]["effective"]["sources"]["/config/language"], "project")
        notes = await project.components.aget("notes")
        await notes.aconfigure({'config': {'language': 'en'}}, expected_version=view["component_versions"]["notes"])
        self.assertEqual((await project.aget_data()).config.parameters["components"]["notes"], {"config": {"language": "en"}})
        with self.assertRaises(ValueError):
            await notes.aconfigure({'config': {'language': 'stale'}}, expected_version=view["component_versions"]["notes"])
        self.assertFalse((project.paths.root / "custom_notes/component.json").exists())
        await project.components.aremove("notes")
        self.assertNotIn("notes", (await project.aconfiguration())["components"])

    async def test_removed_sections_rejected_without_rewriting_persisted_data(self):
        project = await self.app.projects.acreate()
        path = project.paths.root / "project.json"
        saved = read_json(path)
        for key in ("completion", "engines", "component_configurations", "session_defaults", "default_engine"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    ProjectConfig({key: {}})
                with self.assertRaisesRegex(ValueError, key):
                    ProjectConfig.validate_session({key: {}})
                data = {**saved, "config": {key: {}}}
                atomic_json(path, data)
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, key):
                    await self.app.projects.aload(project.id)
                self.assertEqual(path.read_bytes(), before)
        atomic_json(path, saved)

    async def test_session_stores_only_its_explicit_overrides(self):
        provider = ScriptedCompletion([chunk("one", finish="stop")], [chunk("two", finish="stop")])
        self.app.engines.register("writer", LoopEngine(completion_fn=provider))
        config = ProjectConfig(parameters={"engines": {"writer": {'config': {'completion': {'model': 'test', 'temperature': 0.3}}, 'policy': {'request_timeout': 300}}}})
        project = await self.app.projects.acreate(config=config)
        session = await project.sessions.acreate(config={"parameters": {"engines": {"writer": {'policy': {'request_timeout': 60}}}}})
        self.assertNotIn("completion", (await session.aget_data()).config["parameters"]["engines"]["writer"])
        first = await (await session.run.submit("one", engine="writer")).wait()
        self.assertEqual(str((await first.aresult()).status), "completed")
        config.parameters["engines"]["writer"]["config"]["completion"]["temperature"] = .7
        await project.asave(config=config)
        second = await (await session.run.submit("two", engine="writer")).wait()
        self.assertEqual(str((await second.aresult()).status), "completed")
        self.assertEqual([p["temperature"] for p in provider.requests], [.3, .7])
        self.assertTrue(all("timeout" not in p for p in provider.requests))

    async def test_policy_and_component_override_cannot_be_smuggled_through_session(self):
        project = await self.app.projects.acreate()
        for config in ({"policies": {}}, {"parameters": {"components": {"notes": {}}}}):
            with self.assertRaises(ValueError):
                await project.sessions.acreate(config=config)
        self.assertEqual(await project.sessions.alist(), [])

    async def test_target_parameters_are_not_broadcast_and_host_metadata_matches(self):
        first = ScriptedCompletion([chunk("first", finish="stop")])
        second = ScriptedCompletion([chunk("second", finish="stop")])
        self.app.engines.register("writer", LoopEngine(completion_fn=first))
        self.app.engines.register("reviewer", LoopEngine(completion_fn=second))
        project = await self.app.projects.acreate(components=["notes"], config=ProjectConfig(parameters={
            "engines": {"writer": {'config': {'system_prompt': None, 'completion': {'model': 'test/writer', 'temperature': None}}},
                        "reviewer": {'config': {'completion': {'model': 'test/reviewer', 'temperature': 0.8}}}},
            "components": {"notes": {'config': {'model': 'notes/data', 'temperature': 999}}},
        }))
        view = await project.aconfiguration()
        effective = view["effective_engines"]["writer"]
        for path in ("/config/system_prompt", "/config/completion/temperature"):
            self.assertEqual(effective["sources"][path], "project")
            self.assertTrue(effective["editable"][path])
        self.assertIsNone(effective["values"]["config"]["system_prompt"])
        self.assertIsNone(effective["values"]["config"]["completion"]["temperature"])
        fields = view["schema"]["properties"]["config"]["properties"]["parameters"]["properties"]["engines"]["properties"]["writer"]["properties"]
        self.assertNotIn("x-host-override", fields["config"]["properties"]["system_prompt"])
        session = await project.sessions.acreate()
        for name in ("writer", "reviewer"):
            run = await (await session.run.submit(name, engine=name)).wait()
            self.assertEqual(str((await run.aresult()).status), "completed")
        self.assertEqual(first.requests[0]["model"], "test/writer")
        self.assertEqual(second.requests[0]["model"], "test/reviewer")
        self.assertIsNone(first.requests[0]["temperature"])
        self.assertEqual(second.requests[0]["temperature"], .8)
        self.assertNotIn("system", [m["role"] for m in first.requests[0]["messages"]])
        self.assertEqual(view["components"]["notes"]["configuration"]["config"]["temperature"], 999)

    async def test_unconfigured_engine_does_not_borrow_another_engines_model(self):
        provider = ScriptedCompletion([chunk("must not run", finish="stop")])
        self.app.engines.register("other", LoopEngine(completion_fn=provider))
        project = await self.app.projects.acreate(config=ProjectConfig(parameters={
            "engines": {"loop": {'config': {'completion': {'model': 'test'}}}}}))
        self.assertEqual((await project.aconfiguration())["effective_engines"]["other"]["values"], {})
        session = await project.sessions.acreate()
        run = await (await session.run.submit("go", engine="other")).wait()
        result = await run.aresult()
        self.assertEqual(str(result.status), "failed")
        self.assertIn("model is required", result.error)
        self.assertEqual(provider.requests, [])

    async def test_failed_creation_rolls_back_and_shutdown_still_rejects(self):
        with patch.object(self.app.project_manager, "_create", side_effect=OSError("before create")):
            with self.assertRaises(OSError):
                await self.app.projects.acreate()
        self.assertEqual(await self.app.projects.alist(), [])
        project = await self.app.projects.acreate()
        await self.app.shutdown()
        with self.assertRaises(RuntimeError):
            await self.app.projects.acreate()
        with self.assertRaises(RuntimeError):
            await project.aconfiguration()
