"""Unified RAG evidence, required storage fields and atomic publication."""
from tests.llm.configuration_fixtures import rag_settings, rag_project

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from llm.components.rag import RAGComponent, EmbeddingModel
from llm.services.infrastructure.storage import atomic_json, read_json
from llm.llm import LargeLanguageModel
from tests.llm.test_rag_components import fake_embedding


class ChainExtractor:
    async def extract(self, chunks):
        names = {name: name for name in ("Alice", "Atlas", "Seoul", "Other", "Elsewhere")}
        edges = []
        for chunk in chunks:
            for source, target in (("Alice", "Atlas"), ("Atlas", "Seoul"),
                                   ("Seoul", "Alice"), ("Other", "Elsewhere")):
                quote = f"{source} links {target}"
                if quote in chunk["text"]:
                    edges.append({"source": source, "target": target, "type": "links",
                                  "source_id": chunk["id"], "evidence": quote})
        return {"entities": [{"id": name, "name": name} for name in names], "relations": edges}


@unittest.skipUnless(all(importlib.util.find_spec(n) for n in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class UnifiedRAGTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.embedding = EmbeddingModel(model="test/embedding", embedding_fn=fake_embedding)
        self.rag = RAGComponent(embedding=self.embedding, extractor=ChainExtractor())
        self.backend = LargeLanguageModel(self.root, engines={}, components=[self.rag])
        self.addAsyncCleanup(self.backend.shutdown)
        self.project = await self.backend.projects.acreate("Unified RAG", components=["rag"], config=rag_project())
        self.data = await self.project.components.aget("rag")

    async def test_related_graph_depth_limits_cycles_and_sources(self):
        for identifier, text in (("start", "needle Alice links Atlas"), ("next", "Atlas links Seoul"),
                                 ("cycle", "Seoul links Alice"), ("unrelated", "Other links Elsewhere")):
            await self.data.aadd_document(identifier=identifier, title=identifier, content=text)
        result = await self.data.asearch("needle", method="bm25", limit=1, max_hops=1)
        self.assertEqual([r["document_id"] for r in result["relations"]], ["start"])
        result = await self.data.asearch("needle", method="bm25", limit=1, max_hops=2)
        self.assertEqual({r["document_id"] for r in result["relations"]}, {"start", "next"})
        result = await self.data.asearch("needle", method="bm25", limit=1, max_hops=5)
        self.assertEqual(len(result["relations"]), 3)
        sources = {source["id"]: source for source in result["sources"]}
        for edge in result["relations"]:
            self.assertIn(edge["evidence"], sources[edge["source_id"]]["text"])
            self.assertEqual(sources[edge["source_id"]]["revision"], 1)
        bounded = await self.data.asearch("needle", method="bm25", limit=1, relation_limit=1)
        self.assertEqual(len(bounded["relations"]), 1)
        empty = await self.data.asearch("zzzz", method="bm25")
        self.assertEqual(empty, {"query": "zzzz", "documents": [], "entities": [], "relations": [], "sources": [],
                                 "graph_complete": True, "graph_incomplete_documents": []})

    async def test_missing_graph_data_is_rejected_without_rewriting_the_generation(self):
        await self.data.aadd_document(identifier="doc", title="Doc", content="needle Alice links Atlas")
        root = self.project.paths.root / "rag"
        active = read_json(root / "active.json")
        path = root / "generations" / active["generation"] / "corpus.json"
        corpus = read_json(path)
        corpus["documents"]["doc"].pop("graph")
        atomic_json(path, corpus)
        with self.assertRaisesRegex(ValueError, "require graph"):
            await self.data.alist_documents()
        with self.assertRaisesRegex(ValueError, "require graph"):
            await self.data.asearch("needle", method="bm25")
        self.assertEqual(read_json(path), corpus)
        self.assertEqual(read_json(root / "active.json"), active)

    async def test_missing_graph_index_does_not_return_empty_relations(self):
        await self.data.aadd_document(identifier="doc", title="Doc", content="needle Alice links Atlas")
        root = self.project.paths.root / "rag"
        active = read_json(root / "active.json")
        path = root / "generations" / active["generation"] / "graph.kuzu"
        path.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "graph.kuzu"):
            await self.data.asearch("needle", method="bm25")
        with self.assertRaisesRegex(FileNotFoundError, "graph.kuzu"):
            await self.data.agraph_search("Alice")
        self.assertFalse(path.exists())

    async def test_vector_only_document_cannot_replace_the_active_generation(self):
        await self.data.aadd_document(identifier="kept", title="Kept", content="needle Alice links Atlas")
        document = await self.rag.with_config(self.project.data).prepare("invalid", "Invalid", "needle", {}, 1)
        document.pop("graph")
        manager = self.backend.project_manager
        project = manager.load(self.project.id)
        with manager.ownership.scope():
            previous = self.rag.snapshot(project)
            with self.assertRaises(KeyError):
                self.rag.publish(project, previous, {"invalid": document})
            self.assertEqual(self.rag.snapshot(project)["generation"], previous["generation"])
        self.assertEqual([d["id"] for d in await self.data.alist_documents()], ["kept"])

    async def test_extractor_required_before_embedding_and_no_partial_publish(self):
        with patch.object(self.rag, "extractor", None), patch.object(self.embedding, "embed", AsyncMock()) as embed:
            with self.assertRaisesRegex(ValueError, "extractor"):
                await self.data.aadd_document(title="New", content="text")
            embed.assert_not_awaited()
        with patch.object(self.rag.extractor, "extract", AsyncMock(side_effect=RuntimeError("extract failed"))):
            with self.assertRaisesRegex(RuntimeError, "extract failed"):
                await self.data.aadd_document(title="New", content="text")
        self.assertEqual(await self.data.alist_documents(), [])
