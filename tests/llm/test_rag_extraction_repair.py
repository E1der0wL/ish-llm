"""모델의 잘못된 JSON/출처 복구와 실제 Kuzu 가중치·프롬프트 수명 연결을 검사한다."""
from tests.llm.configuration_fixtures import rag_settings, rag_project

import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from llm.components.prompts import PromptComponent
from llm.components.rag import RAGComponent, EmbeddingModel, TripleExtractor, RAGConflictError
from llm.components.rag.extraction import validate_graph
from llm.components.rag.prompts import default_prompt, RELATION_TYPES
from llm.llm import LargeLanguageModel, ProjectConfig
from llm.services.infrastructure.storage import read_json, atomic_json
from tests.llm.test_rag_components import fake_embedding


def response(value):
    return {"choices": [{"message": {"content": value if isinstance(value, str) else json.dumps(value)}}]}


def graph(chunks):
    return {"entities": [{"id": "a", "name": "Atlas"}, {"id": "h", "name": "Harbor"}],
            "relations": [{"source": "a", "target": "h", "type": "USES", "source_id": c["id"],
                           "evidence": "Atlas uses Harbor.", "metadata": {"topic": "storage"}}
                          for c in chunks if "Atlas uses Harbor." in c["text"]]}


class ExtractionRepairTests(unittest.IsolatedAsyncioTestCase):
    chunks = [{"id": "manual:c1", "document_id": "manual", "text": "Atlas uses Harbor."}]

    async def test_both_reported_errors_are_repaired_with_latest_json_and_chunks(self):
        valid = graph(self.chunks)
        bad_quote, bad_entity = deepcopy(valid), deepcopy(valid)
        bad_quote["relations"][0]["evidence"] = "invented quotation"
        bad_entity["relations"][0]["target"] = "missing"
        call = AsyncMock(side_effect=[response(bad_quote), response(bad_entity), response(valid)])
        extractor = TripleExtractor(model="test", temperature=.9, completion_fn=call).with_extraction({"repair_attempts": 2, "relation_types": list(RELATION_TYPES)}, default_prompt())
        result = await extractor.extract(self.chunks)
        self.assertEqual(result, valid)
        self.assertEqual(call.await_count, 3)
        for attempt, request in enumerate(call.call_args_list):
            self.assertEqual(request.kwargs["temperature"], .9)
            if attempt:
                payload = json.loads(request.kwargs["messages"][-1]["content"])
                self.assertEqual(payload["chunks"], self.chunks)
                self.assertEqual(json.loads(payload["previous_json"]), [bad_quote, bad_entity][attempt - 1])
                self.assertIn(["exact source quotation", "unknown entity"][attempt - 1], payload["validation_error"])
            self.assertIn("USES", request.kwargs["messages"][0]["content"])
        self.assertEqual(extractor.params["temperature"], .9)

    async def test_invalid_json_and_non_object_relations_get_bounded_repairs(self):
        call = AsyncMock(side_effect=[response('```json broken'), response({"entities": [], "relations": [3]}),
                                     response(graph(self.chunks))])
        await TripleExtractor(model="test", completion_fn=call).with_extraction({"repair_attempts": 2}, default_prompt()).extract(self.chunks)
        self.assertEqual(call.await_count, 3)

    async def test_disabled_and_exhausted_repairs(self):
        for attempts in (0, 1, 2):
            with self.subTest(attempts=attempts):
                call = AsyncMock(return_value=response({"entities": [], "relations": [{}]}))
                extractor = TripleExtractor(model="test", completion_fn=call).with_extraction(
                    {**{"repair_attempts": 2, "json_mode": "strict", "relation_types": list(RELATION_TYPES), "failure_policy": "required"}, "repair_attempts": attempts}, default_prompt())
                with self.assertRaisesRegex(ValueError, f"after {attempts} repair"):
                    await extractor.extract(self.chunks)
                self.assertEqual(call.await_count, attempts + 1)

    async def test_transport_and_cancellation_are_not_validation_retries(self):
        for error in (RuntimeError("network disconnected"), asyncio.CancelledError()):
            call = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)):
                await TripleExtractor(model="test", completion_fn=call).with_extraction({"repair_attempts": 2}, default_prompt()).extract(self.chunks)
            self.assertEqual(call.await_count, 1)

    def test_malformed_fields_are_validation_errors_not_type_errors(self):
        for field, value in (("source", []), ("target", {}), ("source_id", []), ("metadata", []),
                             ("metadata", {"n": float('nan')}), ("evidence", " ")):
            invalid = graph(self.chunks)
            invalid["relations"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_graph(invalid, self.chunks)


class ComponentExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests = []
        self.invalid = False
        self.mutate_prompt = None

        async def extract(**request):
            self.requests.append(deepcopy(request))
            if self.mutate_prompt:
                hook, self.mutate_prompt = self.mutate_prompt, None
                await hook()
            chunks = json.loads(request["messages"][-1]["content"])
            if isinstance(chunks, dict):
                chunks = chunks["chunks"]
            result = graph(chunks)
            if self.invalid and result["relations"]:
                result["relations"][0]["target"] = "missing"
            # 같은 응답의 중복 관계와 허위 서버 메타데이터는 무시해야 한다.
            if result["relations"]:
                result["relations"][0].update(weight=999, document_id="forged", extracted_at="yesterday")
                result["relations"].append(deepcopy(result["relations"][0]))
            return response(result)

        self.rag = RAGComponent(embedding=EmbeddingModel(model="test/embed", embedding_fn=fake_embedding),
                                extractor=TripleExtractor(model="test/extract", completion_fn=extract))
        self.app = LargeLanguageModel(self.temp.name, components=[self.rag, PromptComponent()])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=["rag", "prompts"], config=rag_project({"parameters": {"components": {"rag": {'policy': {'extraction': {'repair_attempts': 2}}}}}}))
        self.data = await self.project.components.aget("rag")
        self.prompts = await self.project.components.aget("prompts")

    async def add(self, identifier="doc", content="Atlas uses Harbor."):
        return await self.data.aadd_document(identifier=identifier, title=identifier, content=content)

    async def test_weight_provenance_update_delete_and_reopen(self):
        await self.add(content="Atlas uses Harbor.\n\nAtlas uses Harbor.")
        result = await self.data.asearch("Atlas", method="bm25")
        self.assertEqual(len(result["relations"]), 2)
        for edge in result["relations"]:
            self.assertEqual(edge["support_count"], 2)
            self.assertEqual(edge["document_id"], "doc")
            self.assertEqual(edge["metadata"], {"topic": "storage"})
            self.assertIsNotNone(datetime.fromisoformat(edge["extracted_at"]).utcoffset())
        await self.add("other")
        result = await self.data.agraph_search("Atlas")
        self.assertEqual({r["support_count"] for r in result["relations"]}, {3})
        await self.data.aupdate_document("doc", content="Atlas uses Harbor.")
        self.assertEqual({r["support_count"] for r in (await self.data.agraph_search("Atlas"))["relations"]}, {2})
        await self.data.adelete_document("other")
        self.assertEqual({r["support_count"] for r in (await self.data.agraph_search("Atlas"))["relations"]}, {1})
        await self.app.shutdown()
        async with LargeLanguageModel(self.temp.name, components=[self.rag, PromptComponent()]) as app:
            data = await (await app.projects.aload(self.project.id)).components.aget("rag")
            edges = (await data.agraph_search("Atlas"))["relations"]
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0]["support_count"], 1)

    async def test_failed_repairs_preserve_published_document_and_record_usage(self):
        await self.add()
        self.invalid = True
        before = await self.data.aget_document("doc")
        with self.assertRaisesRegex(ValueError, "after 2 repair"):
            await self.data.aupdate_document("doc", content="Atlas uses Harbor. Again.")
        self.assertEqual(await self.data.aget_document("doc"), before)
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(len([r for r in await self.data.amodel_usage() if r["operation"] == "acompletion"]), 4)

    async def test_prompt_crud_project_settings_and_conflict(self):
        custom = {"messages": [{"role": "system", "content": "Custom instructions"}], "extra": {"v": 1}}
        await self.prompts.acreate(custom, identifier="extract")
        await self.data.aconfigure(rag_settings({'policy': {'extraction': {'repair_attempts': 0}}, 'config': {'extraction': {'prompt_id': 'extract', 'relation_types': ['USES', 'CUSTOM']}, 'extraction_params': {'temperature': 0.8}}}))
        await self.add()
        self.assertTrue(any(m["content"] == "Custom instructions" for m in self.requests[-1]["messages"]))
        self.assertEqual(self.requests[-1]["temperature"], 0.8)
        self.assertIn("CUSTOM", self.requests[-1]["messages"][0]["content"])
        effective = await self.data.aeffective_configuration()
        self.assertEqual(effective["values"]["config"]["extraction_params"]["temperature"], 0.8)
        self.assertEqual(effective["sources"]["/config/extraction_params/temperature"], "project")
        async def change():
            await self.prompts.aupdate("extract", {"messages": [{"role": "system", "content": "Changed"}]})
        self.mutate_prompt = change
        with self.assertRaisesRegex(RAGConflictError, "configuration changed"):
            await self.data.aupdate_document("doc", content="Atlas uses Harbor. Again.")
        self.assertEqual((await self.data.aget_document("doc"))["revision"], 1)
        await self.prompts.adelete("extract")
        with self.assertRaises(FileNotFoundError):
            await self.add("other")
        await self.data.aconfigure(rag_settings({}))
        await self.add("other")

    async def test_schema_and_missing_prompt_component(self):
        for extraction in ({"repair_attempts": -1}, {"repair_attempts": True}, {"repair_attempts": 1.5},
                           {"relation_types": [1]}, {"prompt_id": "../escape"}):
            with self.subTest(extraction=extraction), self.assertRaises(ValueError):
                await self.data.aconfigure(rag_settings({'config': {'extraction': extraction}}))
        await self.data.aconfigure(rag_settings({'policy': {'extraction': {'repair_attempts': 2}}, 'config': {'extraction': {'prompt_id': 'extract'}}}))
        await self.project.components.aselect(["rag"])
        with self.assertRaisesRegex(ValueError, "requires selecting"):
            await self.add()

    async def test_graph_schema_one_is_written_and_other_versions_are_rejected(self):
        await self.add()
        root = self.project.paths.root / "rag"
        path = root / "generations" / read_json(root / "active.json")["generation"] / "corpus.json"
        value = read_json(path)
        self.assertEqual(value["graph_schema_version"], 1)
        for version in (None, 2, 99):
            with self.subTest(version=version):
                invalid = dict(value)
                if version is None:
                    invalid.pop("graph_schema_version")
                else:
                    invalid["graph_schema_version"] = version
                atomic_json(path, invalid)
                with self.assertRaisesRegex(ValueError, "new RAG Project"):
                    await self.data.asearch("Atlas", method="bm25")
                self.assertEqual(read_json(path), invalid)
