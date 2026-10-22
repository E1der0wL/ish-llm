"""같은 ID를 재사용해도 이전 generation/revision으로 새 자료를 변경하지 못한다."""

from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.providers.operations import LiteLLMOperations

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.memory import MemoryComponent, MemoryConflictError
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.rag.component import RAGConflictError
from llm.components.rag.jobs import RAGJobs
from llm.engines.loop import LoopEngine
from llm.services.composition import BackendServices
from llm.services.runtime.tools import ToolRuntime, ToolApprovalRequired
from tests.llm.configuration_fixtures import rag_project, configure_engine
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm.test_rag_components import fake_embedding, Extractor


def expected(record):
    return {"expected_generation": record["generation"], "expected_revision": record["revision"]}


class MemoryGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        def authorize(invocation):
            raise ToolApprovalRequired("review")
        self.app = LargeLanguageModel(self.root, components=[MemoryComponent()], engines={},
            services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize)))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=["memory"])
        self.memory = self.project.components.memory

    async def create(self, identifier="item", **content):
        await self.memory.acreate({"content": "source", **content}, identifier=identifier)
        return await self.memory.aload(identifier)

    async def replace(self, record):
        deleted = await self.memory.adelete(record["id"], **expected(record))
        await self.memory.apurge(record["id"], **expected(deleted))
        return await self.create(record["id"], content="new incarnation")

    def match_record_manifest(self, path):
        # Exercise the semantic generation guard independently of byte integrity.
        manifest_path = path.parent.parent / MemoryComponent.RECORDS_MANIFEST
        manifest = json.loads(manifest_path.read_bytes())
        manifest["records"][path.stem] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))

    async def test_stale_generation_rejects_every_record_mutation(self):
        old = await self.create()
        new = await self.replace(old)
        self.assertEqual(old["revision"], new["revision"])
        self.assertNotEqual(old["generation"], new["generation"])
        for method, args in ((self.memory.asave, ({"content": "wrong"},)),
                             (self.memory.aupdate, ({"content": "wrong"},)), (self.memory.adelete, ())):
            with self.subTest(method=method.__name__), self.assertRaises(MemoryConflictError):
                await method("item", *args, **expected(old))
        self.assertEqual(await self.memory.aload("item"), new)
        deleted = await self.memory.adelete("item", **expected(new))
        stale = {**expected(deleted), "expected_generation": old["generation"]}
        for method in (self.memory.arestore, self.memory.apurge):
            with self.subTest(method=method.__name__), self.assertRaises(MemoryConflictError):
                await method("item", **stale)
        restored = await self.memory.arestore("item", **expected(deleted))
        self.assertEqual(restored["generation"], new["generation"])
        self.assertEqual([r["data"]["generation"] for r in await self.memory.ahistory("item")], [new["generation"]] * 3)

    async def test_consolidation_rejects_recreated_source_and_candidate(self):
        old = await self.create("source")
        candidate = await self.create("proposal", status="candidate", replaces=[
            {key: old[key] for key in ("id", "generation", "revision")}])
        new = await self.replace(old)
        with self.assertRaises(MemoryConflictError):
            await self.memory.aconsolidate("proposal", **expected(candidate))
        self.assertEqual(await self.memory.aload("source"), new)
        self.assertEqual(await self.memory.aload("proposal"), candidate)
        self.assertEqual(await self.memory.apending_consolidations(), [])
        await self.replace(candidate)
        with self.assertRaises(MemoryConflictError):
            await self.memory.aconsolidate("proposal", **expected(candidate))

    async def test_generation_is_required_managed_and_history_consistent(self):
        record = await self.create()
        with self.assertRaises(TypeError):
            await self.memory.aupdate("item", {"content": "x"}, expected_revision=1)
        for value in (None, "", "wrong", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.memory.aupdate("item", {"content": "x"}, expected_revision=1, expected_generation=value)
        for changes in ({"generation": "0" * 32}, {"replaces": [{"id": "item", "revision": 1}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await self.memory.aupdate("item", changes, **expected(record))
        await self.memory.aupdate("item", {"content": "next"}, **expected(record))
        path = self.project.paths.root / "memory/records/item.json"
        value = json.loads(path.read_text())
        value["history"][0]["data"]["generation"] = "0" * 32
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "integrity"):
            await self.memory.aload("item")
        self.match_record_manifest(path)
        with self.assertRaisesRegex(ValueError, "generations"):
            await self.memory.aload("item")

    async def test_missing_stored_generation_is_not_materialized(self):
        await self.create()
        path = self.project.paths.root / "memory/records/item.json"
        value = json.loads(path.read_text())
        del value["record"]["generation"]
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "integrity"):
            await self.memory.aload("item")
        self.match_record_manifest(path)
        with self.assertRaisesRegex(ValueError, "generation"):
            await self.memory.aload("item")
        self.assertEqual(path.read_bytes(), before)

    async def test_summary_clear_checks_generation_after_recreation(self):
        session = await self.project.sessions.acreate(agent_ids=sorted(await self.project.agents.alist()))
        async def publish(content):
            snapshot = await self.memory._async_call(self.memory._processing_snapshot, session.id)
            value = {"content": content, "metadata": {"coverage_count": 1, "coverage_hash": "fixture",
                "profile": "fixture", "through_message_id": "last", "first_message_id": "first"}}
            return await self.memory._async_call(self.memory._publish_summary, snapshot, value,
                                                session_id=session.id, source={})
        old = await publish("old")
        await self.memory.aclear_summary(session.id, **expected(old))
        new = await publish("new")
        self.assertEqual(old["revision"], new["revision"])
        self.assertNotEqual(old["generation"], new["generation"])
        with self.assertRaises(MemoryConflictError):
            await self.memory.aclear_summary(session.id, **expected(old))
        self.assertEqual(await self.memory.asummary(session.id), new)
        await self.memory.aclear_summary(session.id, **expected(new))
        self.assertIsNone(await self.memory.asummary(session.id))

    async def test_saved_tool_approval_cannot_delete_recreated_memory(self):
        old = await self.create()
        model = ScriptedCompletion([chunk(calls=[call(json.dumps({"identifier": "item", **expected(old)}),
            name="memory_delete")], finish="tool_calls")], [chunk("done", finish="stop")])
        self.app.engines.register("loop", LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)))
        await configure_engine(self.project, "loop", completion={"model": "test/model"})
        session = await self.project.sessions.acreate(agent_ids=sorted(await self.project.agents.alist()))
        await self.project.agents.acreate({"engine": "loop", "tools": ["memory_delete"]}, identifier="loop")
        await session.asave(agent_ids=sorted(set((await session.aget_data()).agent_ids) | {"loop"}))
        paused = (await (await session.run.submit("delete", recipients=["loop"])).wait())[0]
        self.assertEqual(paused.data.status, "paused", paused.data.error)
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond("approve"))
        new = await self.replace(old)
        resumed = (await (await session.run.resume(paused.id, agent_id="loop")).wait())[0]
        self.assertEqual(resumed.data.status, "failed")
        self.assertEqual(resumed.data.error_code, "memory_conflict")
        self.assertEqual(await self.memory.aload("item"), new)
        self.assertEqual((await paused.aget_data()).status, "paused")


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class RAGGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        async def embed(**request):
            self.calls.append(request)
            return await fake_embedding(**request)
        self.component = RAGComponent(embedding=EmbeddingModel(model="test/embedding", adapter=LiteLLMOperations(call=embed)), extractor=Extractor())
        self.app = LargeLanguageModel(self.root, engines={}, components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=["rag"], config=rag_project())
        self.rag = self.project.components.rag

    async def add(self, text="Alice owns Atlas"):
        return await self.rag.aadd_document(identifier="manual", title="Manual", content=text)

    async def test_recreated_document_rejects_stale_update_and_delete_before_model_call(self):
        old = await self.add()
        await self.rag.adelete_document("manual", **expected(old))
        new = await self.add("new content")
        self.assertEqual(old["revision"], new["revision"])
        self.assertNotEqual(old["generation"], new["generation"])
        calls = len(self.calls)
        active = self.component.corpus_version(self.project.data)
        with self.assertRaises(RAGConflictError):
            await self.rag.aupdate_document("manual", content="stale", **expected(old))
        with self.assertRaises(RAGConflictError):
            await self.rag.adelete_document("manual", **expected(old))
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.component.corpus_version(self.project.data), active)
        self.assertEqual(await self.rag.aget_document("manual"), new)
        updated = await self.rag.aupdate_document("manual", content="current", **expected(new))
        self.assertEqual(updated["generation"], new["generation"])
        self.assertEqual(updated["revision"], 2)

    async def test_cas_requires_complete_typed_pair_but_unconditional_api_remains(self):
        doc = await self.add()
        for values in ({"expected_revision": 1}, {"expected_generation": doc["generation"]},
                       {**expected(doc), "expected_revision": True}, {**expected(doc), "expected_generation": "bad"}):
            for method in (self.rag.aupdate_document, self.rag.adelete_document):
                with self.subTest(values=values, method=method.__name__), self.assertRaises(ValueError):
                    await method("manual", **values)
        updated = await self.rag.aupdate_document("manual", content="explicit unconditional update")
        self.assertEqual(updated["generation"], doc["generation"])
        await self.rag.adelete_document("manual")

    async def test_search_clone_backup_and_reopen_preserve_document_pair(self):
        doc = await self.add()
        result = await self.rag.asearch("Alice", method="bm25")
        for hit in [*result["documents"], *result["sources"]]:
            self.assertEqual((hit["generation"], hit["revision"]), (doc["generation"], doc["revision"]))
        clone = await self.project.aclone()
        self.assertEqual(await clone.components.rag.aget_document("manual"), doc)
        backup = await self.project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "restore", components=[self.component]) as other:
            restored = await other.projects.arestore_backup(backup)
            self.assertEqual(await restored.components.rag.aget_document("manual"), doc)
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, components=[self.component]) as reopened:
            project = await reopened.projects.aload(self.project.id)
            self.assertEqual(await project.components.rag.aget_document("manual"), doc)

    async def test_job_retry_reuses_prepared_generation(self):
        job = await self.rag.aenqueue_document(identifier="manual", title="Manual", content="job source")
        with patch.object(RAGJobs, "_commit", side_effect=OSError("commit unavailable")):
            with self.assertRaises(OSError):
                await self.rag.arun_job(job["id"])
        jobs = RAGJobs(self.rag)
        prepared = await self.rag._async_call(jobs._prepared, job["id"])
        calls = len(self.calls)
        await self.rag.arun_job(job["id"], retry=True)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(await self.rag.aget_document("manual"), {k: v for k, v in prepared.items()
            if k not in ("vectors", "graph", "profile", "preparation_configuration", "preparation_fingerprint", "extraction_manifest")})

    async def test_missing_stored_generation_fails_without_repair(self):
        await self.add()
        generation = self.component.corpus_version(self.project.data)[1]
        path = self.project.paths.root / "rag/generations" / generation / "corpus.json"
        value = json.loads(path.read_text())
        del value["documents"]["manual"]["generation"]
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "generation"):
            await self.rag.aget_document("manual")
        self.assertEqual(path.read_bytes(), before)

    async def test_backup_and_restore_reject_invalid_document_versions(self):
        from llm.services.infrastructure.backups import DirectoryBackups
        await self.add()
        generation = self.component.corpus_version(self.project.data)[1]
        path = self.project.paths.root / "rag/generations" / generation / "corpus.json"
        original = path.read_bytes()
        invalid_versions = (
            {}, {"revision": 1}, {"generation": "0" * 32},
            {"generation": None, "revision": 1}, {"generation": "invalid", "revision": 1},
            *({"generation": "0" * 32, "revision": value} for value in (0, -1, True, "1")),
        )
        async with LargeLanguageModel(self.root / "restore", components=[self.component]) as other:
            for index, versions in enumerate(invalid_versions):
                with self.subTest(versions=versions):
                    corpus = json.loads(original)
                    document = corpus["documents"]["manual"]
                    del document["generation"], document["revision"]
                    document.update(versions)
                    path.write_text(json.dumps(corpus))
                    before = path.read_bytes()
                    destination = self.root / f"invalid-backup-{index}"
                    with self.assertRaisesRegex(ValueError, "generation and positive revision"):
                        await self.project.abackup(destination)
                    self.assertFalse(destination.exists())
                    self.assertEqual(path.read_bytes(), before)

                    # 구형/손상 자료의 체크섬은 올바른 archive를 구성한다.
                    # 복원 거부는 transport checksum이 아닌 Component 계약 검증이어야 한다.
                    archive = DirectoryBackups().create(self.project.paths.root,
                        self.root / f"archive-{index}", metadata={"project_id": self.project.id})
                    archived = archive / "project" / path.relative_to(self.project.paths.root)
                    with self.assertRaisesRegex(ValueError, "generation and positive revision"):
                        await other.projects.arestore_backup(archive)
                    self.assertEqual(await other.projects.alist(), [])
                    self.assertEqual(archived.read_bytes(), before)
            path.write_bytes(original)
