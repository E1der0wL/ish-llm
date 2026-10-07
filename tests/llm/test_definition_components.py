"""새 정의 컴포넌트의 저장·검증·복제·Run capability 연결을 검증한다."""

import asyncio
from pathlib import Path
import tempfile
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.workflows import WorkflowGraph, validate_graph
from llm.core.models import RunStatus
from llm.engines.base import BaseEngine
from llm.services.infrastructure.storage import atomic_json


def serial_graph():
    return (WorkflowGraph(entry="work", metadata={"label": "예제"})
            .node("work", "agent", agent="reviewer", metadata={"future": {"x": 1}})
            .node("done", "end").connect("work", "done").to_dict())


def control_graph():
    """분기 → 병렬 → 합류 → 제한된 반복이 한 문서에 들어가는 예제."""
    return (WorkflowGraph(entry="route")
            .node("route", "branch", cases=[{
                "port": "review", "when": {"path": "/review", "op": "eq", "value": True},
            }], default="skip")
            .node("fork", "parallel", join="joined")
            .node("left", "agent", agent="reviewer")
            .node("right", "retrieval", corpus="manual")
            .node("joined", "join", wait="all")
            .node("refine", "loop", max_iterations=3, on_limit="continue",
                  body=serial_graph(), **{"while": {"path": "/retry", "op": "eq", "value": True}})
            .node("done", "end")
            .connect("route", "fork", port="review").connect("route", "done", port="skip")
            .connect("fork", "left").connect("fork", "right")
            .connect("left", "joined").connect("right", "joined")
            .connect("joined", "refine").connect("refine", "done").to_dict())


def definitions():
    return {
        "agents": {"engine": "loop", "purpose": "코드 검토", "completion": {"model": "test/model", "future": True},
                   "system_prompt": "Review code", "resources": {"skills": ["review"]}},
        "skills": {"instructions": "정확성을 확인한다.", "resources": [{"uri": "docs/design.md"}]},
        "mcp": {"transport": "stdio", "command": "never-run-this", "args": ["--server"], "env": {"X": "1"}},
        "rag": {"metadata": {"sources": [{"id": "doc", "text": "Alice works on ish"}],
                     "entities": [{"id": "alice", "source_ids": ["doc"]}, {"id": "ish"}],
                     "relations": [{"source": "alice", "target": "ish", "type": "works_on"}],
                     "embedding": {"model": "test/embedding", "dimensions": 256}}},
        "workflows": control_graph(),
    }


class DefinitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_component_document_examples_match_production_contracts(self):
        document = Path(__file__).resolve().parents[2] / "docs/llm/component-definitions.md"
        blocks = re.findall(r"```python\n(.*?)\n```", document.read_text(encoding="utf-8-sig"), re.DOTALL)
        namespace = {}
        for block in blocks:
            exec(compile(block, str(document), "exec"), namespace)
        project = await namespace["create_definitions"](self.app)
        agents = await namespace["edit_definitions"](project)
        self.assertEqual(agents["reviewer"]["completion"]["temperature"], 0.1)
        graph = await namespace["create_workflow"](project)
        self.assertEqual(graph, await project.components.workflows.aload("review_flow"))
        self.assertEqual(graph["metadata"]["title"], "검토 Workflow")
        from llm.components.workflows.graph import validate_handler_options
        seen = []
        async def search(query):
            seen.append(query)
            return {"documents": []}
        handler = namespace["RetrievalNode"](search)
        validate_handler_options(graph["nodes"]["search"], handler)
        self.assertEqual(await handler(SimpleNamespace(definition=graph["nodes"]["search"])),
                         {"evidence": {"documents": []}})
        self.assertEqual(seen, ["제품 설명서"])
        for field in ("system_promt", "ui"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                await project.components.agents.aupdate("reviewer", {field: "invalid"})
        self.assertEqual(await project.components.rag.alist_documents(), [])

    def test_workflow_constructor_requires_explicit_metadata_container(self):
        metadata = {"title": "검토 Workflow", "ui": {"label": "검토"}}
        builder = WorkflowGraph(entry="done", metadata=metadata)
        metadata["title"] = "not saved"
        graph = builder.node("done", "end").to_dict()
        self.assertEqual(graph["metadata"]["title"], "검토 Workflow")
        self.assertEqual(WorkflowGraph.from_dict(graph).to_dict(), graph)
        for fields in ({"title": "invalid"}, {"foo": 1}, {"metadata": "invalid"}, {"nodes": {}}, {"schema_version": 1}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                WorkflowGraph(entry="done", **fields)

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = LargeLanguageModel(self.root, engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = self.app.projects.create("definitions", components=list(definitions()))

    async def test_default_availability_creates_only_selected_directories(self):
        empty = self.app.projects.create("empty")
        for name in (*definitions(), "tools"):
            self.assertFalse((empty.paths.root / name).exists())
        empty.components.select(["agents", "skills"])
        self.assertTrue((empty.paths.root / "agents" / "records").is_dir())
        self.assertFalse((empty.paths.root / "workflows").exists())

    async def test_all_components_crud_roundtrip_unknown_keys_and_clone(self):
        for name, definition in definitions().items():
            data = self.project.components[name]
            record = {**definition, "metadata": {"custom": {"label": "한글", "values": [1, None]}}}
            with self.assertRaises(ValueError):
                data.configure({'config': {'future_setting': {'v': 1}}})
            data.create(record, identifier="example")
            loaded = data.load("example")
            self.assertEqual(loaded, record)
            loaded["metadata"]["custom"]["values"].append("detached")
            self.assertEqual(data.load("example"), record)
            data.update("example", {"metadata": {"extra": True}})
        clone = self.project.clone()
        for name in definitions():
            data, copied = self.project.components[name], clone.components[name]
            self.assertEqual(data.list(), copied.list())
            self.assertEqual(data.get_config(), copied.get_config())
            data.delete("example")
            self.assertEqual(data.list(), {})
            self.assertIn("example", copied.list())

    async def test_async_crud_uses_lifecycle_and_shutdown_guard(self):
        for name, value in definitions().items():
            handle = await self.project.components.aget(name)
            identifier = await handle.acreate(value)
            self.assertEqual(await handle.aload(identifier), value)
            await handle.aupdate(identifier, {"metadata": {"future": 2}})
            replacement = {**value, "metadata": {"replacement": True}}
            await handle.asave(identifier, replacement)
            self.assertEqual(await handle.alist(), {identifier: replacement})
            await handle.adelete(identifier)
        handle = await self.project.components.aget("skills")
        await self.app.shutdown()
        with self.assertRaises(RuntimeError):
            await handle.alist()

    async def test_invalid_records_do_not_replace_saved_values(self):
        invalid = {
            "agents": [{"engine": "loop", "purpose": "x", "system_promt": "typo"}, {"completion": {"model": "x"}},
                       {"engine": "loop", "purpose": "x", "completion": {"model": "x"}, "resources": {"skills": [1]}}],
            "skills": [{}, {"instructions": ""}, {"instructions": 1}],
            "mcp": [{"transport": "stdio"}, {"transport": "http", "url": "https://example.test"},
                    {"transport": "streamable_http", "url": "file:///tmp/server"},
                    {"transport": "stdio", "command": "x", "env": {"X": 3}}],
            "workflows": [{"schema_version": 2}, {"schema_version": 1, "nodes": {}, "edges": []}],
        }
        for name, values in invalid.items():
            handle = self.project.components[name]
            original = definitions()[name]
            handle.create(original, identifier="valid")
            for value in values:
                with self.subTest(component=name, value=value):
                    with self.assertRaises(ValueError):
                        handle.save("valid", value)
                    self.assertEqual(handle.load("valid"), original)

    async def test_mcp_transports_are_data_not_connections(self):
        data = self.project.components.mcp
        with patch("subprocess.Popen", side_effect=AssertionError("must not launch")):
            for transport in ("stdio", "streamable_http", "sse"):
                value = {"transport": transport, "command": "unavailable",
                         "url": "https://example.invalid/mcp", "metadata": {"extra": {"timeout": 1}}}
                data.create(value, identifier=transport)
            with self.app.project_manager.ownership.scope():
                snapshots = self.app.project_manager.components.resolve(self.project.data, "mcp")
            self.assertEqual(len(snapshots[0]["records"]), 3)

    async def test_rag_definitions_remain_open_and_do_not_index_documents(self):
        handle = self.project.components.rag
        value = {"metadata": {"sources": [{"id": "x"}], "custom": {"policy": "future"}}}
        identifier = handle.create(value)
        self.assertEqual(handle.load(identifier), value)
        self.assertEqual(await handle.alist_documents(), [])

    async def test_removal_invalidates_existing_handle_and_preserves_unselected_data(self):
        for name, value in definitions().items():
            handle = self.project.components[name]
            handle.create(value, identifier="kept")
            self.project.components.remove(name)
            with self.assertRaises(ValueError):
                handle.load("kept")
            self.assertTrue((self.project.paths.root / name / "records" / "kept.json").exists())

    async def test_workflow_handle_validates_and_restores_builder(self):
        data = self.project.components.workflows
        identifier = await data.acreate(control_graph())
        await data.avalidate(identifier)
        graph = await data.agraph(identifier)
        self.assertEqual(graph.to_dict(), control_graph())
        with self.assertRaises(ValueError):
            data.create({"nodes": [], "custom": 1})
        with self.assertRaises(ValueError):
            data.save(identifier, {"nodes": []})
        self.assertEqual(data.load(identifier), control_graph())

    async def test_definitions_reach_engine_as_detached_requested_snapshots(self):
        class Inspect(BaseEngine):
            required_capabilities = ("agents", "skills", "mcp", "rag", "workflows")
            async def run(engine, context):
                engine.context = context
                yield context.capabilities["agents"][0]["records"]["example"]["purpose"]
        engine = Inspect()
        self.app.engines.register("inspect", engine)
        for name, definition in definitions().items():
            self.project.components[name].create(definition, identifier="example")
        session = await self.project.sessions.acreate("read definitions")
        request = await session.run.submit("read", engine="inspect")
        run = await asyncio.wait_for(request.wait(), 10)
        self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
        self.assertEqual((await run.aresponse()).content, "코드 검토")
        self.assertEqual(set(engine.context.capabilities), set(definitions()))
        engine.context.capabilities["agents"][0]["records"]["example"]["purpose"] = "runtime"
        self.assertEqual(self.project.components.agents.load("example")["purpose"], "코드 검토")
        self.assertEqual(len(await run.steps.alist()), 1)

    async def test_unrequested_corrupt_graph_does_not_block_agent_engine(self):
        class Read(BaseEngine):
            required_capabilities = ("agents",)
            async def run(self, context):
                yield "ok"
        self.app.engines.register("read", Read())
        atomic_json(self.project.paths.root / "workflows" / "records" / "invalid.json", {"nodes": []})
        session = self.project.sessions.create()
        run = await (await session.run.submit("read", engine="read")).wait()
        self.assertEqual(run.data.status, RunStatus.COMPLETED)

    async def test_requested_invalid_graph_fails_before_engine_execution(self):
        class Read(BaseEngine):
            required_capabilities = ("workflows",)
            async def run(self, context):
                raise AssertionError("invalid graph must not enter engine")
                yield "unreachable"
        self.app.engines.register("read", Read())
        atomic_json(self.project.paths.root / "workflows" / "records" / "invalid.json", {"nodes": []})
        session = self.project.sessions.create()
        run = await (await session.run.submit("read", engine="read")).wait()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(run.result.error_code, "capability_failed")
        self.assertEqual(run.steps.list(), [])


class WorkflowValidationTests(unittest.TestCase):
    def test_roundtrip_and_builder_duplicate_protection(self):
        original = control_graph()
        self.assertEqual(WorkflowGraph.from_dict(original).to_dict(), original)
        builder = WorkflowGraph(entry="x").node("x", "end")
        with self.assertRaises(ValueError):
            builder.node("x", "end")
        result = builder.to_dict()
        result["nodes"]["x"]["type"] = "broken"
        self.assertEqual(builder.to_dict()["nodes"]["x"]["type"], "end")

    def test_dangling_unreachable_duplicate_and_cycles(self):
        variants = []
        graph = serial_graph()
        graph["edges"][0]["target"] = "missing"
        variants.append(graph)
        graph = serial_graph()
        graph["nodes"]["unused"] = {"type": "end"}
        variants.append(graph)
        graph = serial_graph()
        graph["edges"].append(graph["edges"][0].copy())
        variants.append(graph)
        graph = serial_graph()
        graph["edges"].append({"source": "done", "target": "work"})
        variants.append(graph)
        for graph in variants:
            with self.assertRaises(ValueError):
                validate_graph(graph)

    def test_branch_requires_complete_distinct_ports_and_valid_conditions(self):
        for key, value in (("default", "review"), ("cases", []),
                           ("cases", [{"port": "review", "when": "eval()"}]),
                           ("cases", [{"port": "review", "when": {"path": "x", "op": "exists"}}])):
            graph = control_graph()
            graph["nodes"]["route"][key] = value
            with self.assertRaises(ValueError):
                validate_graph(graph)
        graph = control_graph()
        del graph["edges"][0]["port"]
        with self.assertRaises(ValueError):
            validate_graph(graph)

    def test_conditions_validate_pointer_escaping_and_operator_types(self):
        for condition in ({"path": "/x~2", "op": "exists"},
                          {"path": "/x", "op": "in", "value": 1},
                          {"path": "/x", "op": "eq"},
                          {"path": "/x", "op": "eval", "value": "x"}):
            graph = control_graph()
            graph["nodes"]["route"]["cases"][0]["when"] = condition
            with self.assertRaises(ValueError):
                validate_graph(graph)
        graph["nodes"]["route"]["cases"][0]["when"] = {"path": "/x~1y~0z", "op": "exists"}
        validate_graph(graph)

    def test_parallel_must_reach_join_without_crossing_branches(self):
        graph = control_graph()
        next(edge for edge in graph["edges"] if edge["source"] == "right")["target"] = "done"
        with self.assertRaisesRegex(ValueError, "Every parallel branch"):
            validate_graph(graph)
        graph = control_graph()
        next(edge for edge in graph["edges"] if edge["source"] == "left")["target"] = "right"
        with self.assertRaisesRegex(ValueError, "merge before"):
            validate_graph(graph)

    def test_join_rejects_external_entry_and_orphan(self):
        graph = control_graph()
        next(edge for edge in graph["edges"] if edge.get("port") == "skip")["target"] = "joined"
        with self.assertRaisesRegex(ValueError, "external incoming"):
            validate_graph(graph)
        graph = serial_graph()
        graph["nodes"]["work"]["type"] = "join"
        del graph["nodes"]["work"]["agent"]
        with self.assertRaisesRegex(ValueError, "belong"):
            validate_graph(graph)

    def test_loop_requires_bounds_exit_policy_and_valid_body(self):
        for key, value in (("max_iterations", 0), ("max_iterations", True),
                           ("max_iterations", 1.5), ("on_limit", "retry"),
                           ("body", {"schema_version": 1}), ("while", "eval()")):
            graph = control_graph()
            graph["nodes"]["refine"][key] = value
            with self.assertRaises(ValueError):
                validate_graph(graph)

    def test_arbitrary_action_type_and_extensions_remain_supported(self):
        graph = serial_graph()
        graph["nodes"]["work"]["type"] = "company.custom_operation"
        graph["nodes"]["work"]["new_setting"] = {"nested": [True, None]}
        graph["metadata"]["new_graph_option"] = "future"
        self.assertEqual(WorkflowGraph.from_dict(graph).to_dict(), graph)

    def test_nested_parallel_and_loop_are_valid(self):
        graph = control_graph()
        graph["nodes"]["refine"]["body"] = control_graph()
        graph["nodes"].update({"inner": {"type": "parallel", "join": "inner_join"},
                               "a": {"type": "work"}, "b": {"type": "work"},
                               "inner_join": {"type": "join"}})
        next(edge for edge in graph["edges"] if edge["source"] == "left")["target"] = "inner"
        graph["edges"].extend({"source": source, "target": target} for source, target in
                              (("inner", "a"), ("inner", "b"), ("a", "inner_join"),
                               ("b", "inner_join"), ("inner_join", "joined")))
        validate_graph(graph)

    def test_wrong_versions_non_json_and_nesting_are_rejected(self):
        for version in (True, 1.0, 0, 2, "1"):
            graph = serial_graph()
            graph["schema_version"] = version
            with self.assertRaises(ValueError):
                validate_graph(graph)
        graph = serial_graph()
        graph["extra"] = object()
        with self.assertRaises(TypeError):
            validate_graph(graph)
        graph = serial_graph()
        for _ in range(34):
            graph = {"schema_version": 1, "entry": "loop", "nodes": {
                "loop": {"type": "loop", "body": graph, "max_iterations": 1, "on_limit": "continue"},
                "end": {"type": "end"}}, "edges": [{"source": "loop", "target": "end"}]}
        validate_graph(graph)  # No hidden numeric nesting cap; cycle/loop contracts remain.


if __name__ == "__main__":
    unittest.main()
