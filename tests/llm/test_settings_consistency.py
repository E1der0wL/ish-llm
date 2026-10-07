"""설정의 출처·우선순위와 실제 Engine/Component 실행이 같은 값을 쓰는지 검증한다."""
from tests.llm.configuration_fixtures import rag_settings, rag_project

import asyncio
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from llm.core.configuration import resolve_configuration
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from llm.engines.pipeline import PipelineEngine, PreparationStep
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.agents import AgentComponent
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.rag.component import RAGConflictError
from llm.llm import LargeLanguageModel
from tests.llm.test_loop import ScriptedCompletion, chunk
from tests.llm.test_rag_components import Extractor, fake_embedding


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    def backend(self, *, engines=None, components=()):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = LargeLanguageModel(directory.name, engines=engines or {}, components=components)
        self.addAsyncCleanup(app.shutdown)
        return app

    async def test_provenance_is_detached_and_host_override_is_visible(self):
        stored = {"a": {"x": 2, "extra": [1]}}
        result = resolve_configuration([("common", {"a": {"x": 1}}), ("project", stored)], host={"a": {"x": 3}})
        self.assertEqual(result["values"]["a"]["x"], 3)
        self.assertEqual(result["sources"]["/a/x"], "host")
        self.assertFalse(result["editable"]["/a/x"])
        self.assertEqual(result["overridden"]["/a/x"][-1], {"source": "project", "value": 2})
        result["values"]["a"]["extra"].append(2)
        self.assertEqual(stored["a"]["extra"], [1])

    async def test_agent_narrows_project_and_session_limits(self):
        engine = LoopEngine()
        agent = engine.for_agent({"engine": "writer", "purpose": "write", "completion": {"model": "fake"},
                                  "engine_options": {'policy': {'max_iterations': 4}}})
        view = agent.configuration({"parameters": {"engines": {"writer": {'policy': {'max_iterations': 9}}}}}, "writer",
                                   session_config={"parameters": {"engines": {"writer": {'policy': {'max_iterations': 6}}}}})
        self.assertEqual(view["values"]["policy"]["max_iterations"], 4)
        self.assertNotIn("request_timeout", view["values"])
        self.assertEqual(view["sources"]["/policy/max_iterations"], "agent")
        self.assertEqual(view["overridden"]["/policy/max_iterations"][-1]["source"], "session")

    async def test_ui_does_not_invoke_runtime_factories(self):
        def factory(context):
            raise AssertionError("UI must not call an Engine factory")
        app = self.backend(engines={"loop": LoopEngine(completion_fn=factory)})
        project = await app.projects.acreate()
        view = await project.aconfiguration()
        self.assertEqual(view["effective_engines"]["loop"]["runtime"], [])
        json.dumps(view, allow_nan=False)
        self.assertEqual(view["effective_engines"]["loop"]["values"], {})

    async def test_graph_project_session_limits_and_schema_control_actual_execution(self):
        effects = []
        async def work(node):
            effects.append(node.node_id)
            return {}
        app = self.backend(engines={"graph": GraphEngine(handlers={"work": work})}, components=[WorkflowComponent()])
        project = await app.projects.acreate(components=["workflows"], config={"parameters": {"engines": {"graph": {'policy': {'max_steps': 3, 'timeout_seconds': None}}}}})
        await (await project.components.aget("workflows")).acreate(
            WorkflowGraph(entry="a").node("a", "work").node("end", "end").connect("a", "end").to_dict(), identifier="flow")
        first = await project.sessions.acreate(config={"parameters": {"engines": {"graph": {"policy": {"max_steps": 1}}}}})
        failed = await (await first.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual((await failed.aget_data()).status, "failed")
        second = await project.sessions.acreate(config={"parameters": {"engines": {"graph": {'policy': {'max_steps': 3}}}}})
        view = await second.aconfiguration()
        self.assertEqual(view["effective_engines"]["graph"]["sources"]["/policy/max_steps"], "session")
        done = await (await second.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual((await done.aget_data()).status, "completed")
        self.assertEqual(effects, ["a", "a"])
        self.assertIsNone(app.engines.resolve("graph").max_steps)

    async def test_graph_agent_narrows_limits_and_inherits_project_settings(self):
        engine = GraphEngine(handlers={})
        agent = engine.for_agent({"engine": "nested", "engine_options": {'policy': {'max_parallelism': 2}, 'workflow': 'child'}})
        view = agent.configuration({"parameters": {"engines": {"nested": {'policy': {'max_parallelism': 8, 'timeout_seconds': None, 'max_steps': 42}}}}}, "nested")
        self.assertEqual(view["values"]["policy"]["max_steps"], 42)
        self.assertEqual(view["values"]["policy"]["max_parallelism"], 2)
        self.assertIsNone(view["values"]["policy"]["timeout_seconds"])
        self.assertEqual(agent.workflow, "child")

    async def test_agent_actual_completion_matches_project_session_agent_host_order(self):
        provider = ScriptedCompletion([chunk("ok", finish="stop")])
        worker = LoopEngine(completion_fn=provider)
        app = self.backend(engines={"graph": GraphEngine(handlers={"agent": AgentNode(engines={"writer": worker})})},
                           components=[WorkflowComponent(), AgentComponent()])
        project = await app.projects.acreate(components=["workflows", "agents"], config={"parameters": {"engines": {"writer": {'policy': {'request_timeout': 7}, 'config': {'completion': {'model': 'project', 'temperature': 0.9}}}}}})
        await (await project.components.aget("agents")).acreate({"engine": "writer", "purpose": "write",
            "completion": {"model": "agent", "temperature": 0.3}}, identifier="writer")
        await (await project.components.aget("workflows")).acreate(WorkflowGraph(entry="a").node("a", "agent", agent="writer")
            .node("end", "end").connect("a", "end").to_dict(), identifier="flow")
        session = await project.sessions.acreate(config={"parameters": {"engines": {"loop": {'config': {'completion': {'temperature': 0.5}}}}}})
        run = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual((await run.aget_data()).status, "completed")
        self.assertEqual(provider.requests[0]["temperature"], 0.3)
        self.assertEqual(provider.requests[0]["model"], "agent")
        self.assertNotIn("timeout", provider.requests[0])

    async def test_pipeline_stage_settings_apply_and_host_timeout_remains_fixed(self):
        provider = ScriptedCompletion([chunk("should not run", finish="stop")])
        async def slow(context):
            await asyncio.sleep(0.2)
        pipeline = PipelineEngine({"prepare": PreparationStep("Prepare", slow), "answer": LoopEngine(completion_fn=provider)})
        app = self.backend(engines={"pipeline": pipeline})
        project = await app.projects.acreate(config={"parameters": {"engines": {"pipeline": {'config': {'stages': {'prepare': {'policy': {'timeout_seconds': 0.01}}}}}, "loop": {'config': {'completion': {'model': 'fake'}}}}}})
        view = await project.aconfiguration()
        self.assertEqual(view["effective_engines"]["pipeline"]["stages"]["prepare"]["values"]["policy"]["timeout_seconds"], .01)
        session = await project.sessions.acreate()
        run = await (await session.run.submit("go", engine="pipeline")).wait()
        self.assertEqual((await run.aget_data()).status, "failed")
        self.assertEqual(provider.requests, [])
        fixed = PreparationStep("Fixed", slow)
        self.assertEqual(fixed.configuration({"parameters": {"engines": {"fixed": {'policy': {'timeout_seconds': 0.01}}}}}, "fixed")["values"]["policy"]["timeout_seconds"], .01)

    async def test_rag_project_settings_isolate_chunking_batches_and_search_defaults(self):
        calls = []
        async def embedding(**params):
            calls.append(params)
            return await fake_embedding(**params)
        component = RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=embedding), extractor=Extractor())
        app = self.backend(components=[component])
        first = await app.projects.acreate(components=["rag"], config=rag_project())
        second = await app.projects.acreate(components=["rag"], config=rag_project())
        small, large = await first.components.aget("rag"), await second.components.aget("rag")
        await small.aconfigure(rag_settings({'config': {'chunk_size': 64, 'search': {'method': 'bm25', 'limit': 1}, 'index_batch_size': 1, 'graph': {'max_num_threads': 1}}, 'policy': {'embedding_concurrency': 1}}))
        text = "Alice owns Atlas. " * 12
        a = await small.aadd_document(title="A", content=text)
        b = await large.aadd_document(title="B", content=text)
        self.assertGreater(len(a["chunks"]), len(b["chunks"]))
        self.assertIsNone(component.chunk_size)
        before = len(calls)
        hits = await small.asearch("Alice")
        self.assertEqual(len(hits["documents"]), 1)
        self.assertEqual(len(calls), before)
        view = await first.aconfiguration()
        self.assertEqual(view["components"]["rag"]["effective"]["sources"]["/config/chunk_size"], "project")

    async def test_rag_json_model_configuration_uses_injected_clients_without_mutation(self):
        call = AsyncMock(side_effect=fake_embedding)
        model = EmbeddingModel(embedding_fn=call)
        component = RAGComponent(embedding=model, extractor=Extractor())
        app = self.backend(components=[component])
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        rag = await project.components.aget("rag")
        await rag.aconfigure(rag_settings({'config': {'embedding_params': {'model': 'project-model', 'timeout': 17}}}))
        await rag.aadd_document(title="A", content="Alice owns Atlas")
        self.assertEqual(call.call_args.kwargs["model"], "project-model")
        self.assertEqual(call.call_args.kwargs["timeout"], 17)
        self.assertEqual(model.params, {})
        view = await rag.aeffective_configuration()
        self.assertEqual(view["sources"]["/config/embedding_params/model"], "project")

    async def test_rag_rejects_config_change_during_preparation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def embed(**params):
            entered.set()
            await release.wait()
            return await fake_embedding(**params)
        app = self.backend(components=[RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=embed), extractor=Extractor())])
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        rag = await project.components.aget("rag")
        pending = asyncio.create_task(rag.aadd_document(title="A", content="Alice owns Atlas"))
        await entered.wait()
        await rag.aconfigure(rag_settings({'config': {'chunk_size': 64}}))
        release.set()
        with self.assertRaises(RAGConflictError):
            await pending
        self.assertEqual(await rag.alist_documents(), [])

    async def test_rag_project_configuration_and_invalid_settings(self):
        app = self.backend(components=[RAGComponent()])
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        rag = await project.components.aget("rag")
        await rag.aconfigure(rag_settings({'config': {'chunk_size': 64}}))
        view = await rag.aeffective_configuration()
        self.assertEqual(view["values"]["config"]["chunk_size"], 64)
        self.assertTrue(view["editable"]["/config/chunk_size"])
        for config in ({"chunk_size": 2}, {"search": {"limit": 0}}, {"embedding_concurrency": True}):
            with self.assertRaises(ValueError):
                await rag.aconfigure(rag_settings(config))
        self.assertEqual((await rag.aconfiguration())["config"]["chunk_size"], 64)

    async def test_prepared_job_cannot_publish_after_settings_change(self):
        from llm.components.rag.jobs import RAGJobs
        app = self.backend(components=[RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=fake_embedding), extractor=Extractor())])
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        rag = await project.components.aget("rag")
        job = await rag.aenqueue_document(title="A", content="Alice owns Atlas")
        with patch.object(RAGJobs, "_commit", side_effect=ConnectionError("before publish")):
            with self.assertRaises(ConnectionError):
                await rag.arun_job(job["id"])
        await rag.aconfigure(rag_settings({'config': {'chunk_size': 64}}))
        with self.assertRaisesRegex(ValueError, "settings changed"):
            await rag.arun_job(job["id"], retry=True)
        self.assertEqual(await rag.alist_documents(), [])
