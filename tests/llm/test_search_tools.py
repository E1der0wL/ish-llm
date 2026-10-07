"""검색 Tool과 Loop의 연결을 실제 Chroma/Kuzu 및 결정적 모델 스트림으로 검증한다."""
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.configuration_fixtures import rag_settings, rag_project

import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components.rag import RAGComponent, EmbeddingModel
from llm.core.models import RunStatus
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from tests.llm.test_loop import ScriptedCompletion, call, chunk
from tests.llm.test_rag_components import Extractor, fake_embedding


def completion_for(name, arguments, text="문서에 따라 답변했습니다."):
    return ScriptedCompletion(
        [chunk(calls=[call(json.dumps(arguments), name=name)]), chunk(finish="tool_calls")],
        [chunk(text), chunk(finish="stop")])


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class SearchToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.embedding = EmbeddingModel(model="test/embedding", embedding_fn=fake_embedding)
        self.rag_component = RAGComponent(embedding=self.embedding, extractor=Extractor())
        self.graph_component = self.rag_component
        self.app = LargeLanguageModel(Path(self.temp.name),
                                      components=[self.rag_component], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        # ToolComponent 없이 두 컴포넌트가 tools capability를 제공한다.
        self.project = await self.app.projects.acreate("Search", components=["rag"], config=rag_project())
        self.rag = await self.project.components.aget("rag")
        self.graph = await self.project.components.aget("rag")

    async def run_loop(self, project, completion, *, name="loop"):
        self.app.engines.register(name, LoopEngine(completion_fn=completion).for_agent({"engine": 'loop', "engine_options": LoopEngine.settings_layout.pack({'completion': {"model": "test/model"}})}))
        session = await project.sessions.acreate("Question")
        return await (await session.run.submit("설명서에서 검색해 주세요.", engine=name)).wait(timeout=20)

    async def test_rag_definition_result_transcript_and_persisted_steps(self):
        await self.rag.aadd_document(identifier="manual", title="운영 설명서",
                                     content="## Backup\n\nbackup every 15 minutes.\n\nKeep 30 days.",
                                     metadata={"version": "2.0", "source": "manual.md"})
        arguments = {"query": "backup", "method": "hybrid", "expand": "section", "limit": 1}
        completion = completion_for("rag_search", arguments, "15분마다 백업합니다.")
        run = await self.run_loop(self.project, completion)
        self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
        self.assertEqual((await run.aresponse()).content, "15분마다 백업합니다.")
        self.assertEqual(len(completion.requests), 2)
        definitions = completion.requests[0]["tools"]
        self.assertEqual([t["function"]["name"] for t in definitions],
                         ["rag_search"])
        self.assertNotIn("tool_choice", completion.requests[0])
        self.assertTrue(completion.requests[0]["stream"])
        message = completion.requests[1]["messages"][-1]
        self.assertEqual((message["role"], message["tool_call_id"]), ("tool", "call_1"))
        payload = json.loads(message["content"])
        hit = payload["documents"][0]
        self.assertEqual((hit["document_id"], hit["revision"], hit["metadata"]["version"]), ("manual", 1, "2.0"))
        self.assertIn("Keep 30 days", hit["context"])
        steps = await run.steps.alist()
        self.assertEqual([s.kind for s in steps], ["llm", "tool", "llm"])
        self.assertTrue(all(str(s.status) == "completed" for s in steps))
        step = steps[1]
        self.assertEqual((step.name, step.metadata["arguments"], step.output.data),
                         ("rag_search", arguments, payload))
        # 핸들 메모리뿐 아니라 실제 step.json에도 같은 결과가 남는다.
        saved = json.loads((step.paths.root / "step.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["metadata"]["output"]["data"], payload)

    async def test_single_search_tool_returns_documents_and_versioned_relations(self):
        await self.graph.aadd_document(identifier="manual", title="Ownership",
                                       content="## People\n\nAlice owns Atlas", metadata={"source": "owners.md"})
        completion = ScriptedCompletion(
            [chunk(calls=[call('{"query":"Alice","method":"bm25"}', name="rag_search")]),
             chunk(finish="tool_calls")],
            [chunk("Alice owns Atlas."), chunk(finish="stop")])
        run = await self.run_loop(self.project, completion)
        self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
        names = [t["function"]["name"] for t in completion.requests[0]["tools"]]
        self.assertEqual(names, ["rag_search"])
        first = json.loads(completion.requests[1]["messages"][-1]["content"])
        self.assertEqual(first["documents"][0]["document_id"], "manual")
        result = first
        self.assertEqual(result["relations"][0]["evidence"], "Alice owns Atlas")
        self.assertEqual((result["sources"][0]["revision"], result["sources"][0]["metadata"]["source"]),
                         (1, "owners.md"))
        tool_steps = [s for s in await run.steps.alist() if s.kind == "tool"]
        self.assertEqual(len(tool_steps), 1)
        self.assertEqual(tool_steps[0].output.data, result)

    async def test_selected_components_always_expose_tools_despite_legacy_flags(self):
        for data in (self.rag,):
            self.assertEqual((await data.aconfiguration())["config"]["search"]["method"], "hybrid")
            self.assertFalse(hasattr(data, "enable_search_tools"))
            self.assertFalse(hasattr(data, "aenable_search_tools"))
            # 제거된 실행 설정을 보존하거나 자동 변환하지 않는다.
            with self.assertRaises(ValueError):
                await data.aconfigure({'config': {'search_tools_enabled': False, 'future': {'language': 'ko'}}})
        completion = ScriptedCompletion([chunk("No search needed"), chunk(finish="stop")])
        run = await self.run_loop(self.project, completion)
        self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
        self.assertEqual({item["function"]["name"] for item in completion.requests[0]["tools"]},
                         {"rag_search"})
        self.assertNotIn("search_tools_enabled", (await self.rag.aconfiguration())["config"])

    async def test_project_selection_controls_tools_on_subsequent_runs(self):
        project = await self.app.projects.acreate("Selection", components=[])
        session = await project.sessions.acreate()
        model = ScriptedCompletion(*([chunk("done"), chunk(finish="stop")] for _ in range(4)))
        self.app.engines.register("loop", LoopEngine(completion_fn=model).for_agent({"engine": 'loop', "engine_options": LoopEngine.settings_layout.pack({'completion': {"model": "test/model"}})}))
        for names in ([], ["rag"], ["rag"], []):
            await project.components.aselect(names)
            run = await (await session.run.submit("hello", engine="loop")).wait(timeout=20)
            self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
        self.assertEqual([[item["function"]["name"] for item in req.get("tools", [])]
                          for req in model.requests],
                         [[], ["rag_search"], ["rag_search"], []])

    async def test_two_projects_cannot_select_each_others_corpus(self):
        other = await self.app.projects.acreate("Other", components=["rag"], config=rag_project())
        other_rag = await other.components.aget("rag")
        for handle, label in ((self.rag, "FIRST"), (other_rag, "SECOND")):
            await handle.aadd_document(identifier="manual", title=label, content="backup " + label)
        one = completion_for("rag_search", {"query": "backup", "method": "bm25"})
        two = completion_for("rag_search", {"query": "backup", "method": "bm25"})
        runs = await asyncio.gather(self.run_loop(self.project, one, name="one"),
                                   self.run_loop(other, two, name="two"))
        for run, model, title in zip(runs, (one, two), ("FIRST", "SECOND")):
            self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
            payload = json.loads(model.requests[1]["messages"][-1]["content"])
            self.assertEqual({hit["title"] for hit in payload["documents"]}, {title})

    async def test_local_and_external_search_tools_coexist_without_schema_collisions(self):
        from llm.components.tools import ToolComponent
        from llm.components.tools.builtin import BuiltinTools
        async def external(arguments):
            return {"external_corpus": arguments["corpus"], "matches": ["remote document"]}
        work = Path(self.temp.name) / "work"
        work.mkdir()
        async with BuiltinTools(work,
                                adapters={"external_rag_search": external}) as toolkit:
            self.app.project_manager.components.register(RuntimeTools(toolkit.registry))
            await self.project.components.aselect(["rag", "tools"])
            tools = await self.project.components.aget("tools")
            await tools.aenable("external_rag_search")
            model = ScriptedCompletion(
                [chunk(calls=[call('{"query":"q"}', name="rag_search"),
                              call('{"corpus":"remote","query":"q"}', name="external_rag_search",
                                   index=1, call_id="call_2")]), chunk(finish="tool_calls")],
                [chunk("done"), chunk(finish="stop")])
            run = await self.run_loop(self.project, model)
            self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
            definitions = {t["function"]["name"]: t["function"]["parameters"] for t in model.requests[0]["tools"]}
            self.assertNotIn("corpus", definitions["rag_search"]["properties"])
            self.assertIn("corpus", definitions["external_rag_search"]["required"])
            results = [json.loads(m["content"]) for m in model.requests[1]["messages"] if m["role"] == "tool"]
            self.assertEqual(results[0]["documents"], [])
            self.assertEqual(results[1]["external_corpus"], "remote")

    async def test_empty_results_are_success_and_invalid_arguments_never_execute(self):
        completion = completion_for("rag_search", {"query": "unknown"})
        run = await self.run_loop(self.project, completion, name="empty")
        self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
        self.assertEqual(json.loads(completion.requests[1]["messages"][-1]["content"])["documents"], [])
        invalid = completion_for("rag_search", {"query": "q", "project_id": "another"})
        run = await self.run_loop(self.project, invalid, name="invalid")
        self.assertEqual((await run.aget_data()).status, RunStatus.FAILED)
        self.assertEqual(len(invalid.requests), 1)
        self.assertFalse(any(s.kind == "tool" for s in await run.steps.alist()))

    async def test_search_failure_persists_failed_tool_not_an_empty_result(self):
        completion = completion_for("rag_search", {"query": "backup"})
        with patch.object(self.rag_component, "snapshot", side_effect=OSError("index unavailable")):
            run = await self.run_loop(self.project, completion)
        self.assertEqual((await run.aget_data()).status, RunStatus.FAILED)
        step = next(s for s in await run.steps.alist() if s.kind == "tool")
        self.assertEqual(str(step.status), "failed")
        self.assertIn("index unavailable", step.error)
        self.assertEqual(len(completion.requests), 1)

    async def test_reopen_ignores_legacy_false_and_exposes_search_without_setup(self):
        await self.rag.aadd_document(identifier="doc", title="Guide", content="backup every 15 minutes")
        with self.assertRaises(ValueError):
            await self.rag.aconfigure(rag_settings({'config': {'search_tools_enabled': False}}))
        project_id = self.project.id
        await self.app.shutdown()
        completion = completion_for("rag_search", {"query": "backup", "method": "bm25"})
        async with LargeLanguageModel(Path(self.temp.name),
                                      components=[self.rag_component],
                                      engines={"loop": LoopEngine(completion_fn=completion).for_agent({"engine": 'loop', "engine_options": LoopEngine.settings_layout.pack({'completion': {"model": "test/model"}})})}) as app:
            project = await app.projects.aload(project_id)
            rag = await project.components.aget("rag")
            self.assertTrue(await rag.asearch("backup", method="bm25"))
            session = await project.sessions.acreate()
            run = await (await session.run.submit("search", engine="loop")).wait(timeout=20)
            self.assertEqual((await run.aget_data()).status, RunStatus.COMPLETED)
            self.assertTrue(json.loads(completion.requests[1]["messages"][-1]["content"])["documents"])

    async def test_interrupt_during_query_embedding_records_interrupted_tool(self):
        await self.rag.aadd_document(title="Guide", content="backup every 15 minutes")
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def blocked(**kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        completion = completion_for("rag_search", {"query": "backup"})
        self.app.engines.register("loop", LoopEngine(completion_fn=completion).for_agent({"engine": 'loop', "engine_options": LoopEngine.settings_layout.pack({'completion': {"model": "test/model"}})}))
        session = await self.project.sessions.acreate()
        with patch.object(self.embedding, "_call_fn", blocked):
            request = await session.run.submit("search", engine="loop")
            await asyncio.wait_for(entered.wait(), 10)
            await session.run.interrupt()
            run = await request.wait(timeout=10)
        self.assertTrue(cancelled.is_set())
        self.assertEqual((await run.aget_data()).status, RunStatus.INTERRUPTED)
        step = next(s for s in await run.steps.alist() if s.kind == "tool")
        self.assertEqual(str(step.status), "interrupted")
        self.assertEqual(len(completion.requests), 1)

    async def test_exported_tool_rechecks_component_selection_and_manager_lifetime(self):
        session = await self.project.sessions.acreate()
        manager = self.app._manager(await session.aget_data())
        registry = await manager._io.run(manager.capabilities.resolve_tools, await self.project.aget_data())
        tool = registry.get("rag_search")
        await self.project.components.aremove("rag")
        with self.assertRaisesRegex(ValueError, "not enabled"):
            await tool.handler({"query": "backup", "method": "bm25"})
        await self.project.components.aselect(["rag"])
        await manager.shutdown()
        with self.assertRaisesRegex(RuntimeError, "shut down"):
            await tool.handler({"query": "backup"})
