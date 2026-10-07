from llm.core.schema import implementation_schema
from tests.llm.support.runtime_tools import RuntimeTools
"""Project 설정 전달, 단일 저장 경계, 공개 편의 API와 실제 실행을 검증한다."""
from tests.llm.configuration_fixtures import rag_settings, rag_project

import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from jsonschema import Draft202012Validator
from llm.llm import LargeLanguageModel, ProjectConfig, LoopEngine, ToolComponent, ToolRegistry, Tool
from llm.components.base import Component
from llm.components.memory import MemoryComponent
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.services.composition import BackendServices
from llm.services.runtime.output import OutputBuffer, consume_events
from llm.engines.graph import GraphEngine
from tests.llm.test_loop import ScriptedCompletion, chunk
from tests.llm.test_rag_components import Extractor, fake_embedding


class ProjectComponentSettingsTests(unittest.IsolatedAsyncioTestCase):
    def backend(self, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        app = LargeLanguageModel(temp.name, **kwargs)
        self.addAsyncCleanup(app.shutdown)
        return app

    async def test_convenience_configuration_always_writes_project_only(self):
        app = self.backend(components=[MemoryComponent(), ToolComponent()])
        project = await app.projects.acreate(components=["memory", "tools"])
        memory = await project.components.aget("memory")
        before = await project.adescribe_config()
        await memory.aconfigure({'config': {'search_limit': 3}})
        tools = await project.components.aget("tools")
        await tools.acreate({"source": "# editable source"}, identifier="offline_definition")
        await tools.aenable("offline_definition")
        saved = json.loads((project.paths.root / "project.json").read_text())
        self.assertEqual(saved["config"]["parameters"]["components"], {
            "memory": {"config": {"search_limit": 3}},
            "tools": {"config": {"enabled": ["offline_definition"]}}})
        self.assertEqual(list(project.paths.root.rglob("component.json")), [])
        self.assertNotIn("component_configurations", (await project.adescribe_config())["values"])
        with self.assertRaises(ValueError):
            await project.asave(config=before["project"]["config"], expected_version=before["config_version"])
        await memory.aconfigure({})
        self.assertNotIn("search_limit", await memory.aget_config())
        self.assertNotIn("extra", await memory.aget_config())

    async def test_project_model_parameters_override_injected_client_defaults(self):
        requests = []
        async def embed(**params):
            requests.append(params)
            return await fake_embedding(**params)
        model = EmbeddingModel(model="client-default", embedding_fn=embed, timeout=99)
        app = self.backend(components=[RAGComponent(embedding=model, extractor=Extractor())])
        project = await app.projects.acreate(components=["rag"], config=rag_project(ProjectConfig(parameters={"components": {"rag": {'config': {'embedding_params': {'model': 'project-model', 'timeout': 7}}}}})))
        rag = await project.components.aget("rag")
        await rag.aadd_document(title="Manual", content="Alice owns Atlas")
        self.assertEqual((requests[0]["model"], requests[0]["timeout"]), ("project-model", 7))
        self.assertEqual(model.params, {"model": "client-default", "timeout": 99})
        self.assertEqual((await rag.aresolve_config())["sources"]["/config/embedding_params/model"], "project")

    def test_removed_direct_configuration_apis_are_not_available(self):
        from llm.components.registry import ComponentRegistry
        from llm.components.tools import ToolPaths
        self.assertFalse(hasattr(Component, "configure"))
        self.assertFalse(hasattr(Component, "stored_configuration"))
        self.assertFalse(hasattr(ComponentRegistry, "configure"))
        self.assertFalse(hasattr(ToolPaths, "configuration"))
        self.assertFalse(hasattr(ToolComponent, "enable"))
        with self.assertRaises(TypeError):
            RAGComponent(chunk_size=64)

    async def test_project_rag_parameters_reach_model_and_retrieval(self):
        requests = []
        async def embed(**kwargs):
            requests.append(kwargs)
            return await fake_embedding(**kwargs)
        app = self.backend(components=[RAGComponent(
            embedding=EmbeddingModel(embedding_fn=embed), extractor=Extractor())])
        project = await app.projects.acreate(components=["rag"], config=rag_project(ProjectConfig(parameters={"components": {"rag": {'config': {'chunk_size': 64, 'embedding_params': {'model': 'project-model'}, 'search': {'method': 'bm25', 'limit': 1}}, 'policy': {'embedding_concurrency': 1}}}})))
        rag = await project.components.aget("rag")
        document = await rag.aadd_document(title="Manual", content="Alice owns Atlas. " * 12)
        self.assertGreater(len(document["chunks"]), 1)
        self.assertTrue(all(r["model"] == "project-model" and len(r["input"]) == 1 for r in requests))
        count = len(requests)
        result = await rag.asearch("Alice")
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(len(requests), count)
        view = await project.adescribe_config()
        self.assertEqual(view["components"]["rag"]["effective"]["sources"]["/config/chunk_size"], "project")
        Draft202012Validator(view["schema"]).validate(view["values"])

    async def test_tool_conveniences_edit_project_source_and_execution_uses_selection(self):
        tools = ToolRegistry((Tool("echo", "Echo", {"type": "object"}, lambda args: args),))
        provider = ScriptedCompletion([chunk("done", finish="stop")])
        app = self.backend(components=[RuntimeTools(tools)], engines={"loop": LoopEngine(completion_fn=provider)})
        project = await app.projects.acreate(components=["tools"], config=ProjectConfig(parameters={"engines": {"loop": {'config': {'completion': {'model': 'test'}}}}, "components": {"tools": {'config': {'enabled': []}}}}))
        data = await project.components.aget("tools")
        path = project.paths.root / "tools" / "component.json"
        self.assertFalse(path.exists())
        await data.aenable("echo")
        self.assertEqual((await project.aget_data()).config.parameters.setdefault("components", {})["tools"]['config']['enabled'], ["echo"])
        session = await project.sessions.acreate()
        run = await (await session.run.submit("hello", engine="loop")).wait()
        self.assertEqual((await run.aget_data()).status, "completed")
        self.assertEqual(provider.requests[0]["tools"][0]["function"]["name"], "echo")
        await data.adisable("echo")
        self.assertEqual(await data.aenabled(), [])
        self.assertFalse(path.exists())

    async def test_save_validate_before_write_and_detect_stale_component_edit(self):
        app = self.backend(components=[MemoryComponent(), RAGComponent()])
        project = await app.projects.acreate(components=["memory", "rag"], config=rag_project(ProjectConfig(parameters={"components": {"memory": {'config': {'search_limit': 3}}}})))
        view = await project.adescribe_config()
        original = (project.paths.root / "project.json").read_bytes()
        settings = ProjectConfig(view["project"]["config"])
        settings.parameters.setdefault("components", {}).update({"memory": {'config': {'search_limit': 4}}, "rag": {'config': {'chunk_size': 0}}})
        with self.assertRaises(ValueError):
            await project.asave(config=settings)
        self.assertEqual((project.paths.root / "project.json").read_bytes(), original)
        memory = await project.components.aget("memory")
        await memory.aconfigure({'config': {'search_limit': 5}})
        with self.assertRaises(ValueError):
            await project.asave(config=view["project"]["config"], expected_version=view["config_version"])

    async def test_clone_reopen_disable_and_remove_keep_settings_ownership(self):
        app = self.backend(components=[MemoryComponent()])
        project = await app.projects.acreate(components=["memory"], config=ProjectConfig(parameters={"components": {"memory": {'config': {'search_limit': 3}}}}))
        clone = await project.aclone()
        reopened = await app.projects.aload(clone.id)
        memory = await reopened.components.aget("memory")
        self.assertEqual((await memory.aget_config())["config"]["search_limit"], 3)
        self.assertFalse((clone.paths.root / "memory" / "component.json").exists())
        await reopened.components.aremove("memory")
        self.assertEqual((await reopened.aget_data()).config.parameters.setdefault("components", {})["memory"]['config']['search_limit'], 3)
        await reopened.components.aselect(["memory"])
        await reopened.components.aremove("memory", permanent=True)
        self.assertNotIn("memory", (await reopened.aget_data()).config.parameters.setdefault("components", {}))

    async def test_registered_inactive_settings_do_not_create_directories(self):
        app = self.backend(components=[RAGComponent()])
        project = await app.projects.acreate(config=ProjectConfig(parameters={"components": {"rag": {'config': {'chunk_size': 64}}}}))
        self.assertFalse((project.paths.root / "rag").exists())
        await project.components.aselect(["rag"])
        self.assertEqual((await (await project.components.aget("rag")).aget_config())["config"]["chunk_size"], 64)
        with self.assertRaises(ValueError):
            await app.projects.acreate(config=ProjectConfig(parameters={"components": {"typo": {}}}))
        with self.assertRaises(ValueError):
            ProjectConfig(session_defaults={"parameters": {"components": {"rag": {}}}})

    async def test_custom_component_validation_and_json_keys_are_preserved(self):
        class Custom(Component):
            name = directory = "custom"
            def describe_config(self):
                return implementation_schema(config={'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1}}})
        app = self.backend(components=[Custom()])
        with self.assertRaises(ValueError):
            await app.projects.acreate(components=["custom"], config=ProjectConfig(parameters={"components": {"custom": {'config': {'limit': 0}}}}))
        project = await app.projects.acreate(components=["custom"], config=ProjectConfig(parameters={"components": {"custom": {'config': {'limit': 7, 'application': {'new': True}}}}}))
        data = await project.components.aget("custom")
        self.assertEqual((await data.aget_config())["config"]["application"], {"new": True})
        settings = (await project.aget_data()).config
        settings.parameters.setdefault("components", {}).clear()
        await project.asave(config=settings)
        self.assertNotIn("limit", await data.aget_config())

    async def test_failed_project_write_does_not_modify_component_files(self):
        app = self.backend(components=[MemoryComponent(), RAGComponent()])
        project = await app.projects.acreate(components=["memory", "rag"], config=rag_project())
        files = [project.paths.root / "project.json"]
        before = [p.read_bytes() for p in files]
        config = (await project.aget_data()).config
        config.parameters.setdefault("components", {}).update({"memory": {'config': {'search_limit': 4}}, "rag": {'config': {'chunk_size': 64}}})
        with patch("llm.services.lifecycle.projects.atomic_domain_json", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                await project.asave(config=config)
        self.assertEqual([p.read_bytes() for p in files], before)

    async def test_graph_binding_cannot_filter_project_component_settings(self):
        app = self.backend(components=[MemoryComponent()])
        project = await app.projects.acreate(components=["memory"], config=ProjectConfig(data={"application": 1}, parameters={"components": {"memory": {'config': {'search_limit': 4}}}}))
        engine = GraphEngine(handlers={}, config_keys=())
        binding = engine._execution_config(SimpleNamespace(project=await project.aget_data()))
        self.assertEqual(binding["parameters"]["components"], {"memory": {"config": {"search_limit": 4}}})
        self.assertNotIn("data", binding)

    async def test_project_output_policy_is_applied_to_run_and_other_project_inherits_host(self):
        provider = ScriptedCompletion([chunk("one"), chunk("two", finish="stop")], [chunk("other", finish="stop")])
        host = OutputBuffer(batch_size=2, max_delay=.04, max_chars=1000)
        app = self.backend(services=BackendServices(output_buffer=host), engines={"loop": LoopEngine(completion_fn=provider)})
        project = await app.projects.acreate(config=ProjectConfig(policies={"output": {"batch_size": 4, "max_chars": 50}}, parameters={"engines": {"loop": {'config': {'completion': {'model': 'test'}}}}}))
        other = await app.projects.acreate(config=ProjectConfig(parameters={"engines": {"loop": {'config': {'completion': {'model': 'test'}}}}}))
        seen = []
        async def consume(events, batching, handler):
            seen.append(batching)
            return await consume_events(events, batching, handler)
        with patch("llm.services.runtime.runs.consume_events", side_effect=consume):
            for item in (project, other):
                session = await item.sessions.acreate()
                run = await (await session.run.submit("go", engine="loop")).wait()
                self.assertEqual((await run.aget_data()).status, "completed")
        self.assertEqual(seen, [OutputBuffer(batch_size=4, max_delay=.04, max_chars=50), host])
        self.assertEqual(app.services.output_buffer, host)
