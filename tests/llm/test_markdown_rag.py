"""RAG 예제는 공개 Component API만 조합하며 DB 구현을 복제하지 않는다."""

from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from examples.llm import rag_components
from llm.components.rag import RAGComponent
from llm.components.rag.extraction import validate_graph
from llm.components.rag import EmbeddingModel
from llm.components.rag.splitting import split_markdown
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from tests.llm.test_loop import ScriptedCompletion, call, chunk
from tests.llm.test_rag_components import Extractor, fake_embedding


FIXTURE = Path(__file__).resolve().parents[2] / "examples/llm/fixtures/rag_operations.md"


class MarkdownRecordsTests(unittest.TestCase):
    def test_fixture_uses_component_splitter_and_preserves_parent_context(self):
        text = FIXTURE.read_text(encoding="utf-8")
        corpus = split_markdown(text, "operations", chunk_size=2000)
        for item in corpus["chunks"]:
            section = corpus["sections"][item["section_id"]]["text"]
            self.assertIn(item["text"], section)
            self.assertIn(section, text)
        backup = next(item for item in corpus["chunks"] if "15분" in item["text"])
        self.assertIn("매주 월요일", corpus["sections"][backup["section_id"]]["text"])

    def test_component_extractor_rejects_invented_evidence(self):
        corpus = split_markdown("Alice owns Atlas", "manual", chunk_size=2000)
        graph = {"entities": [{"id": "a", "name": "Alice"}, {"id": "b", "name": "Atlas"}],
                 "relations": [{"source": "a", "target": "b", "type": "owns",
                                "source_id": corpus["chunks"][0]["id"], "evidence": "invented"}]}
        with self.assertRaisesRegex(ValueError, "exact source quotation"):
            validate_graph(graph, corpus["chunks"])
        graph["relations"][0]["evidence"] = "Alice owns Atlas"
        self.assertEqual(len(validate_graph(graph, corpus["chunks"])["relations"]), 1)

    def test_old_verify_is_not_silently_used_with_new_storage(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            rag_components.main(["--model", "test", "--verify", "old-output"])
        self.assertEqual(error.exception.code, 2)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class ComponentExampleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        path = self.root / "guide.md"
        path.write_text("## Ownership\n\nAlice owns Atlas\n\nbackup every 15 minutes", encoding="utf-8")
        self.args = SimpleNamespace(workspace=self.root / "workspace", markdown=path,
                                    model="test/model", embedding_model="test/embed", query="backup",
                                    seed="Alice", exercise_crud=False, ask=None)

    async def example(self, completion=None):
        embedding = EmbeddingModel(model="test/embed", embedding_fn=fake_embedding)
        def engine(**kwargs):
            return LoopEngine(completion_fn=completion, **kwargs)
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-placeholder"}), \
                patch.object(rag_components, "EmbeddingModel", return_value=embedding), \
                patch.object(rag_components, "TripleExtractor", return_value=Extractor()), \
                patch.object(rag_components, "LoopEngine", side_effect=engine), redirect_stdout(io.StringIO()):
            return await rag_components.run(self.args)

    async def test_public_example_search_loop_steps_and_offline_reopen(self):
        self.args.ask = "Search the manual"
        model = ScriptedCompletion(
            [chunk(calls=[call('{"query":"backup","method":"bm25"}', name="rag_search")]),
             chunk(finish="tool_calls")],
            [chunk("15 minutes"), chunk(finish="stop")])
        report = await self.example(model)
        self.assertEqual(report["answer"], "15 minutes")
        self.assertEqual(report["run"]["status"], "completed")
        self.assertEqual([s["kind"] for s in report["run"]["steps"]], ["llm", "tool", "llm"])
        self.assertEqual(report["graph"]["relations"][0]["target"], "Atlas")
        self.assertTrue(json.loads(model.requests[1]["messages"][-1]["content"])["documents"])
        async with LargeLanguageModel(self.args.workspace, components=[RAGComponent()], engines={}) as app:
            project = await app.projects.aload(report["project_id"])
            data = await project.components.aget("rag")
            hits = await data.asearch_documents("backup", method="bm25", expand="document")
            self.assertIn("Alice owns Atlas", hits[0]["context"])
            self.assertTrue((await data.agraph_search("Alice"))["relations"])

    async def test_crud_example_uses_document_api_and_leaves_no_stale_index(self):
        self.args.exercise_crud = True
        report = await self.example()
        self.assertNotIn("run", report)
        async with LargeLanguageModel(self.args.workspace, components=[RAGComponent()], engines={}) as app:
            project = await app.projects.aload(report["project_id"])
            data = await project.components.aget("rag")
            self.assertEqual(await data.alist_documents(), [])
            self.assertEqual(await data.asearch_documents("backup", method="bm25"), [])
            self.assertEqual((await data.agraph_search("Alice"))["relations"], [])
