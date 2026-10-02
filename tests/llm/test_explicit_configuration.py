"""Explicit-only configuration is a public contract, independent of test presets."""

import asyncio
from contextlib import contextmanager
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from llm.core.configuration import resolve_configuration
from llm.core.models import ProjectConfig
from llm.core.policies import normalize_policies
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.pipeline import PipelineEngine, PreparationStep
from llm.components.rag import RAGComponent, EmbeddingModel, TripleExtractor
from llm.components.memory import MemoryComponent
from llm.components.tools import Tool
from llm.llm import LargeLanguageModel
from llm.providers.calls import ProviderLimits, ProviderCalls
from llm.providers.requests import invoke, ProviderError
from llm.providers.litellm import stream_completion
from llm.services.runtime.tools import ToolExecutor


def chunks(text="ok"):
    yield {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
    yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}


class ConfigurationTests(unittest.TestCase):
    def test_host_override_metadata_matches_effective_values(self):
        cases = [
            (LoopEngine(), "system_prompt", "project prompt", False),
            (LoopEngine(system_prompt=None), "system_prompt", None, True),
            (LoopEngine(system_prompt=""), "system_prompt", "", True),
            (LoopEngine(system_prompt="host prompt"), "system_prompt", "host prompt", True),
            (LoopEngine(request_timeout=None), "request_timeout", None, True),
            (GraphEngine("workflow", handlers={}, timeout_seconds=None), "timeout_seconds", None, True),
            (PreparationStep("prepare", lambda context: None, timeout_seconds=None), "timeout_seconds", None, True),
        ]
        for engine, key, value, overridden in cases:
            with self.subTest(engine=type(engine).__name__, key=key, value=value):
                config = {"engines": {"test": {key: "project prompt" if key == "system_prompt" else 300}}}
                view = engine.configuration(config, "test")
                self.assertEqual(view["values"][key], value)
                self.assertEqual(view["sources"]["/" + key], "host" if overridden else "project")
                self.assertEqual(view["editable"]["/" + key], not overridden)
                self.assertEqual(engine.configuration_schema()["properties"][key]["x-host-override"], overridden)

    def test_pipeline_and_runtime_prompt_override_metadata(self):
        def dynamic_prompt(context):
            raise AssertionError("UI must not execute runtime factories")
        engine = PipelineEngine({"explicit": LoopEngine(system_prompt=None),
                                 "dynamic": LoopEngine(system_prompt=dynamic_prompt)})
        config = {"engines": {"pipeline": {"stages": {
            name: {"system_prompt": "project prompt"} for name in ("explicit", "dynamic")}}}}
        view = engine.configuration(config, "pipeline")["stages"]
        schema = engine.configuration_schema()["properties"]["stages"]["properties"]
        self.assertIsNone(view["explicit"]["values"]["system_prompt"])
        self.assertEqual(view["explicit"]["sources"]["/system_prompt"], "host")
        self.assertNotIn("system_prompt", view["dynamic"]["values"])
        self.assertEqual(view["dynamic"]["sources"]["/system_prompt"], "host_runtime")
        for name in view:
            self.assertFalse(view[name]["editable"]["/system_prompt"])
            self.assertTrue(schema[name]["properties"]["system_prompt"]["x-host-override"])

    def test_sparse_resolution_and_null(self):
        for session, host, expected, source in (({}, {}, 300, "project"),
                ({"timeout": 60}, {}, 60, "session"),
                ({"timeout": None}, {}, None, "session"),
                ({"timeout": None}, {"timeout": 10}, 10, "host")):
            result = resolve_configuration([("project", {"timeout": 300}), ("session", session)], host=host)
            self.assertEqual(result["values"], {"timeout": expected})
            self.assertEqual(result["sources"]["/timeout"], source)
        result = resolve_configuration([("project", {}), ("session", {}), ("agent", {})])
        self.assertEqual(result["values"], {})
        self.assertEqual(result["sources"], {})

    def test_empty_policies_and_engines_do_not_materialize(self):
        self.assertEqual(normalize_policies({}), {})
        self.assertEqual(ProjectConfig().policies, {})
        loop = LoopEngine()
        self.assertIsNone(loop.request_timeout)
        self.assertIsNone(loop.tool_timeout)
        self.assertEqual(loop.configuration({}, "loop")["values"], {})
        self.assertEqual(GraphEngine("workflow", handlers={}).configuration({}, "graph")["values"], {})
        self.assertIsNone(GraphEngine("workflow", handlers={}).timeout_seconds)
        self.assertEqual(LoopEngine(request_timeout=None).configuration(
            {"engines": {"loop": {"request_timeout": 300}}}, "loop")["values"], {"request_timeout": None})

    def test_limits_are_inactive_without_host_settings(self):
        limits = ProviderLimits()
        self.assertEqual((limits.max_active, limits.max_waiting, limits.wait_seconds), (None, None, None))
        self.assertIsNone(ToolExecutor().timeout_seconds)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_ui_exposes_explicit_null_host_override(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[], engines={"loop": LoopEngine(system_prompt=None)}) as app:
                project = await app.projects.acreate(config={"engines": {"loop": {"system_prompt": "project prompt"}}})
                view = await project.aconfiguration()
                effective = view["effective_engines"]["loop"]
                self.assertIsNone(effective["values"]["system_prompt"])
                self.assertEqual(effective["sources"]["/system_prompt"], "host")
                self.assertFalse(effective["editable"]["/system_prompt"])
                schema = view["schema"]["properties"]["config"]["properties"]["engines"]["properties"]["loop"]
                self.assertTrue(schema["properties"]["system_prompt"]["x-host-override"])

    async def test_loop_sdk_kwargs_and_wrapper_are_independent(self):
        for options, completion in (({}, {}), ({"request_timeout": .5}, {}),
                                     ({}, {"timeout": None}), ({}, {"timeout": 17})):
            captured = []
            def call(**request):
                captured.append(request)
                time.sleep(.015)
                yield from chunks()
            with tempfile.TemporaryDirectory() as root:
                async with LargeLanguageModel(root, components=[], engines={"loop": LoopEngine(completion_fn=call, **options)}) as app:
                    project = await app.projects.acreate(config=ProjectConfig(completion={"model": "test", **completion}))
                    session = await project.sessions.acreate()
                    run = await (await session.run.submit("hello", engine="loop")).wait(timeout=5)
                    self.assertEqual(run.data.status, "completed", run.data.error)
            self.assertEqual({k: captured[0][k] for k in ("timeout", "temperature", "max_tokens", "num_retries", "max_retries", "stream_options") if k in captured[0]}, completion)
            self.assertIs(captured[0]["stream"], True)

    async def test_explicit_loop_wrapper_timeout(self):
        def call(**request):
            self.assertNotIn("timeout", request)
            time.sleep(.05)
            yield from chunks()
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[], engines={"loop": LoopEngine(completion_fn=call, request_timeout=.005)}) as app:
                project = await app.projects.acreate(config={"completion": {"model": "test"}})
                session = await project.sessions.acreate()
                run = await (await session.run.submit("hello", engine="loop")).wait(timeout=5)
                self.assertEqual(run.data.status, "failed")

    async def test_stream_bridge_adds_no_deadline(self):
        captured = []
        def call(**request):
            captured.append(request)
            return iter(["one"])
        with patch("llm.providers.litellm.asyncio.wait_for", side_effect=AssertionError("hidden bridge deadline")):
            self.assertEqual([item async for item in stream_completion({"model": "test"}, completion_fn=call)], ["one"])
        self.assertEqual(captured, [{"model": "test"}])

    async def test_step_deadline_does_not_cancel_event_persistence(self):
        from types import SimpleNamespace
        from llm.engines.base import BaseEngine
        closed = []
        async def action(context):
            try:
                yield "first"
                await asyncio.sleep(1)
            finally:
                closed.append(True)
        context = SimpleNamespace(run=SimpleNamespace(id="run"), output_step_id=None, output_visibility="user")
        stream = BaseEngine(action=action, timeout_seconds=.01).execute(context)
        self.assertEqual((await stream.__anext__()).type.value, "step_started")
        self.assertEqual((await stream.__anext__()).type.value, "text_delta")
        # 서비스의 저장/구독자 처리를 재현한다. Engine timeout이 이 Task를 취소하면 안 된다.
        await asyncio.sleep(.03)
        self.assertEqual((await stream.__anext__()).type.value, "step_failed")
        with self.assertRaises(asyncio.TimeoutError):
            await stream.__anext__()
        self.assertEqual(closed, [True])

    async def test_provider_retry_is_opt_in_and_sdk_retries_are_preserved(self):
        for options, request, expected in (({}, {}, 1), ({"max_attempts": 3}, {}, 3),
                ({"max_attempts": 4}, {"num_retries": 2, "max_retries": 3}, 1)):
            call = AsyncMock(side_effect=ConnectionError("offline"))
            with self.assertRaises(ProviderError):
                await invoke("aembedding", request, call, options)
            self.assertEqual(call.await_count, expected)
            self.assertEqual(call.call_args.kwargs, request)

    async def test_tool_optional_deadline_and_cancellation(self):
        async def slow(args):
            await asyncio.sleep(.02)
            return None
        tool = Tool("slow", "slow", {"type": "object"}, slow)
        result = {}
        events = [e async for e in ToolExecutor().execute(tool, {}, result=result)]
        self.assertEqual(result["value"], None)
        self.assertEqual(events[-1].type.value, "step_completed")
        with self.assertRaises(Exception):
            [e async for e in ToolExecutor(timeout_seconds=.001).execute(tool, {}, result={})]
        entered = asyncio.Event()
        async def wait(args):
            entered.set()
            await asyncio.Event().wait()
        async def consume():
            return [e async for e in ToolExecutor().execute(Tool("wait", "wait", {"type": "object"}, wait), {}, result={})]
        pending = asyncio.create_task(consume())
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending

    async def test_components_remain_sparse_and_rag_requires_explicit_algorithm_settings(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[RAGComponent(), MemoryComponent()], engines={}) as app:
                project = await app.projects.acreate(components=["rag", "memory"])
                for name in ("rag", "memory"):
                    data = await project.components.aget(name)
                    self.assertEqual((await data.aeffective_configuration())["values"], {})
                rag = await project.components.aget("rag")
                with self.assertRaisesRegex(ValueError, "Missing required setting"):
                    await rag.aadd_document(title="doc", content="hello")
                view = await project.aconfiguration()
                def check(value):
                    if isinstance(value, dict):
                        self.assertNotIn("default", value)
                        for child in value.values():
                            check(child)
                    elif isinstance(value, list):
                        for child in value:
                            check(child)
                check(view["schema"])

    async def test_extraction_sampling_and_json_format_are_explicit(self):
        call = AsyncMock(return_value={"choices": [{"message": {"content": '{"entities":[],"relations":[]}'}}]})
        extractor = TripleExtractor(model="test", completion_fn=call)
        await extractor.extract([{"id": "one", "text": "hello"}])
        self.assertNotIn("temperature", call.call_args.kwargs)
        self.assertNotIn("response_format", call.call_args.kwargs)
        self.assertNotIn("timeout", call.call_args.kwargs)

    async def test_graph_and_node_deadlines_are_independent_and_opt_in(self):
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        for graph_options, node_options, expected in (({}, {}, "completed"),
                ({"timeout_seconds": None}, {"timeout_seconds": None}, "completed"),
                ({"timeout_seconds": .003}, {}, "failed"),
                ({}, {"timeout_seconds": .003}, "failed")):
            async def slow(node):
                await asyncio.sleep(.03)
                return {}
            with tempfile.TemporaryDirectory() as root:
                engine = GraphEngine("flow", handlers={"slow": slow}, **graph_options)
                async with LargeLanguageModel(root, components=[WorkflowComponent()], engines={"graph": engine}) as app:
                    project = await app.projects.acreate(components=["workflows"])
                    graph = WorkflowGraph(entry="work").node("work", "slow", **node_options).node("end", "end").connect("work", "end")
                    await project.components.workflows.acreate(graph.to_dict(), identifier="flow")
                    session = await project.sessions.acreate()
                    run = await (await session.run.submit("go", engine="graph")).wait(timeout=5)
                    self.assertEqual(run.data.status, expected, run.data.error)

    async def test_native_library_options_are_omitted_and_explicit_values_pass_through(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from llm.components.rag.graph_indexing import connection
        from llm.components.rag.indexing import collection
        db, client = Mock(), Mock()
        kuzu = SimpleNamespace(Database=Mock(return_value=db), Connection=Mock())
        chroma = SimpleNamespace(PersistentClient=Mock(return_value=client))
        with patch.dict(sys.modules, kuzu=kuzu, chromadb=chroma):
            with connection(Path("graph")):
                pass
            kuzu.Database.assert_called_once_with("graph")
            with connection(Path("graph"), options={"max_num_threads": 2}):
                pass
            self.assertEqual(kuzu.Database.call_args.kwargs, {"max_num_threads": 2})
            with collection(Path("vectors"), create=True):
                pass
            chroma.PersistentClient.assert_called_once_with("vectors")
            client.create_collection.assert_called_once_with("documents", embedding_function=None)

    async def test_provider_wall_timeout_is_not_sdk_timeout(self):
        seen = []
        async def slow(**params):
            seen.append(params)
            await asyncio.sleep(.02)
            return {"data": []}
        await invoke("aembedding", {}, slow, {})
        with self.assertRaises(ProviderError):
            await invoke("aembedding", {}, slow, {"wall_timeout": .001})
        self.assertEqual(seen, [{}, {}])

    async def test_agent_inherits_prompt_and_host_null_removes_it(self):
        definition = {"engine": "loop", "purpose": "business purpose", "completion": {"model": "test"}}
        engine = LoopEngine().for_agent(definition)
        config = {"engines": {"loop": {"system_prompt": "project prompt"}}}
        self.assertEqual(engine.configuration(config, "loop")["values"]["system_prompt"], "project prompt")
        host = LoopEngine(system_prompt=None).for_agent(definition)
        self.assertIsNone(host.configuration(config, "loop")["values"]["system_prompt"])

    async def test_graph_agent_inherits_partial_completion_and_does_not_invent_prompt(self):
        from llm.components.agents import AgentComponent
        from llm.components.skills import SkillComponent
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        from llm.engines.graph.agent import AgentNode
        for overrides, expected_prompt in (({}, "project prompt"),
                ({"system_prompt": None}, None),
                ({"resources": {"skills": ["review"]}}, "project prompt\n\nSkill review:\nCheck facts.")):
            captured = []
            def call(**params):
                captured.append(params)
                return chunks()
            components = [AgentComponent(), WorkflowComponent(), SkillComponent()]
            graph = GraphEngine("flow", handlers={"agent": AgentNode(engines={"loop": LoopEngine(completion_fn=call)})})
            with tempfile.TemporaryDirectory() as root:
                async with LargeLanguageModel(root, components=components, engines={"graph": graph}) as app:
                    project = await app.projects.acreate(components=[c.name for c in components], config={
                        "completion": {"model": "test", "temperature": .8},
                        "engines": {"loop": {"system_prompt": "project prompt"}}})
                    await project.components.agents.acreate({"engine": "loop", "purpose": "Not a system prompt",
                        "completion": {"temperature": 0}, **overrides}, identifier="worker")
                    prompt_view = await project.components.agents.aprompt("worker")
                    self.assertEqual({k: v for k, v in prompt_view.items() if k == "system_prompt"},
                                     {k: v for k, v in overrides.items() if k == "system_prompt"})
                    await project.components.skills.acreate({"instructions": "Check facts."}, identifier="review")
                    flow = (WorkflowGraph(entry="work", inputs={"request": "/prompt"}, outputs={"answer": "/answer"})
                        .node("work", "agent", agent="worker", inputs={"request": "/request"}, outputs={"answer": "/text"})
                        .node("end", "end").connect("work", "end"))
                    await project.components.workflows.acreate(flow.to_dict(), identifier="flow")
                    session = await project.sessions.acreate(config={"completion": {"top_p": .7}})
                    run = await (await session.run.submit("hello", engine="graph")).wait(timeout=5)
                    self.assertEqual(run.data.status, "completed", run.data.error)
                    await project.components.agents.aupdate_prompt("worker", None, expected_revision=prompt_view["revision"])
                    self.assertIsNone((await project.components.agents.aprompt("worker"))["system_prompt"])
            self.assertEqual({k: captured[0][k] for k in ("model", "temperature", "top_p")},
                             {"model": "test", "temperature": 0, "top_p": .7})
            prompts = [m["content"] for m in captured[0]["messages"] if m["role"] == "system"]
            self.assertEqual(prompts, [] if expected_prompt is None else [expected_prompt])

    async def test_registered_components_and_policies_have_no_implicit_leaf_values(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root) as app:
                project = await app.projects.aget_default()
                view = await project.aconfiguration()
                self.assertEqual(project.data.config.policies, {})
                for name, item in view["components"].items():
                    self.assertEqual(item["configuration"], {}, name)
                    self.assertEqual(item["effective"]["values"], {}, name)
                self.assertNotIn('"default":', __import__("json").dumps(view["schema"]))

    async def test_memory_empty_processing_does_not_recall_or_call_model(self):
        from llm.components.memory.processing import processing_settings
        self.assertEqual(processing_settings({}), {})
        with self.assertRaisesRegex(ValueError, "Missing required setting"):
            processing_settings({"processing": {"recall": True}})

    async def test_schema_rejects_default_annotation_but_allows_field_named_default(self):
        from llm.core.schema import checked_schema
        with self.assertRaisesRegex(ValueError, "implicit defaults"):
            checked_schema({"type": "object", "properties": {"timeout": {"type": "number", "default": 120}}})
        checked_schema({"type": "object", "properties": {"default": {"type": "string"}}})

    async def test_explicit_client_policy_inherits_and_project_null_overrides(self):
        from tests.llm.configuration_fixtures import rag_project
        from types import SimpleNamespace
        client = EmbeddingModel(model="test").with_provider({"max_attempts": 3, "wall_timeout": 20})
        component = RAGComponent(embedding=client)
        project = SimpleNamespace(id="one", paths=SimpleNamespace(root=Path("unused")), config=rag_project())
        self.assertEqual(component.configured(project).embedding.provider_options, {"max_attempts": 3, "wall_timeout": 20})
        project.config.component_configurations["rag"]["provider"] = {"wall_timeout": None}
        view = component.effective_configuration(project)["model_providers"]["embedding"]
        self.assertEqual(view["values"], {"max_attempts": 3, "wall_timeout": None})
        self.assertEqual(view["sources"], {"/max_attempts": "client", "/wall_timeout": "project"})
        self.assertIsNone(component.configured(project).embedding.provider_options["wall_timeout"])
        self.assertEqual(client.provider_options["wall_timeout"], 20)

    async def test_host_tool_retry_none_overrides_project_and_missing_inherits(self):
        from llm.services.configuration import ServiceConfig
        from llm.services.runtime.tools import ToolPolicy
        from llm.engines.base import BaseEngine
        for policy, expected in ((ToolPolicy(), 3), (ToolPolicy(max_retries=None), None), (ToolPolicy(max_retries=1), 1)):
            observed = []
            class Inspect(BaseEngine):
                async def run(self, context):
                    observed.append(context.tool_scope.policy.max_retries)
                    yield "ok"
            with tempfile.TemporaryDirectory() as root:
                async with LargeLanguageModel(root, components=[], engines={"inspect": Inspect()}, services=ServiceConfig(tool_policy=policy)) as app:
                    project = await app.projects.acreate(config={"policies": {"tool_retry": {"max_retries": 3}}})
                    session = await project.sessions.acreate()
                    run = await (await session.run.submit("go", engine="inspect")).wait(timeout=5)
                    self.assertEqual(run.data.status, "completed", run.data.error)
            self.assertEqual(observed, [expected])

    async def test_process_worker_does_not_invent_os_resource_limits(self):
        import io
        import json
        import os
        import resource
        from types import SimpleNamespace
        from unittest.mock import Mock
        from llm.services.runtime import _worker
        for memory in (None, 4096):
            process = Mock(returncode=0)
            payload = {"memory_bytes": memory, "argv": ["unused"], "cwd": "/", "env": {}, "payload": {}}
            with patch("ctypes.CDLL", return_value=SimpleNamespace(prctl=lambda *args: 0)), \
                    patch("signal.signal"), patch("resource.setrlimit") as limit, \
                    patch.object(_worker.sys, "argv", ["worker", str(os.getppid())]), \
                    patch.object(_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))), \
                    patch.object(_worker.subprocess, "Popen", return_value=process):
                self.assertEqual(_worker.main(), 0)
                if memory is None:
                    limit.assert_not_called()
                else:
                    limit.assert_called_once_with(resource.RLIMIT_AS, (memory, memory))


if __name__ == "__main__":
    unittest.main()
