"""Explicit caches and incremental ingestion preserve the RAG publication contract."""

from llm.providers.operations import LiteLLMOperations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.rag.component import RAGConflictError
from llm.core.models import ProjectConfig
from llm.llm import LargeLanguageModel
from llm.providers.requests import ProviderError
from tests.llm.configuration_fixtures import rag_project
from tests.llm.test_rag_components import Extractor, fake_embedding


class OptimizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = AsyncMock(side_effect=fake_embedding)
        self.extractor = Extractor()
        self.component = RAGComponent(embedding=EmbeddingModel(model="test/embedding", adapter=LiteLLMOperations(call=self.calls)),
                                      extractor=self.extractor)
        self.app = LargeLanguageModel(Path(self.temp.name), engines={}, components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("optimization", components=["rag"], config=rag_project())
        self.rag = await self.project.components.aget("rag")

    async def configure(self, **values):
        current = (await self.project.aget_data()).config.parameters["components"]["rag"]
        await self.rag.aconfigure(ProjectConfig.merge(current, {"config": values}))

    async def add(self, identifier="manual", content="Alice owns Atlas"):
        return await self.rag.aadd_document(identifier=identifier, title=identifier, content=content)

    async def test_job_admission_scans_other_jobs_only_for_explicit_limit(self):
        from llm.components.rag.jobs import RAGJobs
        jobs = RAGJobs(self.rag)
        first = await self.rag.aenqueue_document(identifier="first", title="First", content="Alice owns Atlas")
        lease = await self.rag._async_call(jobs._lease, first["id"])
        try:
            with patch.object(RAGJobs, "list", side_effect=AssertionError("Unlimited admission must not scan Jobs")):
                await self.rag._async_call(jobs._claim, first["id"], False)
            second = await self.rag.aenqueue_document(identifier="second", title="Second", content="Bob owns Atlas")
            values = await self.rag.aget_config()
            await self.rag.aconfigure(ProjectConfig.merge(values, {"policy": {"ingestion": {"max_active": 1}}}))
            with self.assertRaisesRegex(ValueError, "rag_capacity"):
                await self.rag.arun_job(second["id"])
            self.assertEqual((await self.rag.ajob(second["id"]))["status"], "queued")
            self.assertEqual(self.calls.await_count, 0)
        finally:
            lease.close()

    async def test_query_cache_is_explicit_and_invalidates_on_params(self):
        await self.add()
        self.calls.reset_mock()
        for _ in range(2):
            await self.rag.asearch_documents("Alice")
        self.assertEqual(self.calls.await_count, 2)
        await self.configure(query_cache_max_bytes=8192)
        self.calls.reset_mock()
        for _ in range(2):
            await self.rag.asearch_documents("Alice")
        self.assertEqual(self.calls.await_count, 1)
        await self.configure(query_kwargs={"user": "different"})
        await self.rag.asearch_documents("Alice")
        self.assertEqual(self.calls.await_count, 2)
        self.assertLessEqual(self.component.query_cache.bytes, 8192)
        await self.configure(query_cache_max_bytes=0)
        await self.rag.asearch_documents("Alice")
        self.assertEqual(self.calls.await_count, 3)
        self.assertEqual(self.component.query_cache.bytes, 0)

    async def test_query_cache_validates_hits_and_does_not_cache_failure(self):
        await self.add()
        await self.configure(query_cache_max_bytes=8192)
        await self.rag.asearch_documents("Alice")
        key = next(iter(self.component.query_cache.entries))
        self.component.query_cache.entries[key] = (float("nan"), 1., 1.)
        with self.assertRaises(ValueError):
            await self.rag.asearch_documents("Alice")
        self.component.query_cache.entries[key] = (1., 2.)
        with self.assertRaises(ValueError):
            await self.rag.asearch_documents("Alice")
        self.calls.side_effect = ConnectionError("offline")
        with self.assertRaises(ProviderError):
            await self.rag.asearch_documents("uncached")
        self.calls.side_effect = fake_embedding
        await self.rag.asearch_documents("uncached")

    async def test_query_cache_separates_injected_client_and_project(self):
        await self.add()
        await self.configure(query_cache_max_bytes=8192)
        await self.rag.asearch_documents("Alice")
        replacement = AsyncMock(side_effect=fake_embedding)
        self.component.embedding.adapter.call = replacement
        await self.rag.asearch_documents("Alice")
        self.assertEqual(replacement.await_count, 1)
        other = await self.app.projects.acreate("other", components=["rag"], config=rag_project({"parameters": {
            "components": {"rag": {"config": {"query_cache_max_bytes": 8192}}}}}))
        rag = await other.components.aget("rag")
        await rag.aadd_document(title="Other", content="Alice owns Atlas")
        replacement.reset_mock()
        await rag.asearch_documents("Alice")
        self.assertEqual(replacement.await_count, 1)

    async def test_cache_configuration_has_no_materialized_defaults(self):
        values = self.component.resolve_config(self.project.data)["values"]
        self.assertNotIn("query_cache_max_bytes", values.get("config", {}))
        with self.assertRaises(ValueError):
            await self.configure(query_cache_max_bytes=-1)
        with self.assertRaises(ValueError):
            await self.configure(query_cache_max_bytes=True)

    async def test_metadata_update_skips_models_and_index_mutation_but_advances_revision(self):
        original = await self.add()
        before = self.component.snapshot(self.project.data)
        self.calls.reset_mock()
        with patch.object(self.extractor, "extract", side_effect=AssertionError("unnecessary extraction")), \
             patch("llm.components.rag.indexing.collection", side_effect=AssertionError("unnecessary vector write")), \
             patch("llm.components.rag.graph_indexing.connection", side_effect=AssertionError("unnecessary graph write")):
            updated = await self.rag.aupdate_document("manual", title="Renamed", description="Guide", metadata={"tag": "new"},
                expected_revision=original["revision"], expected_generation=original["generation"])
        self.calls.assert_not_awaited()
        self.assertEqual(updated["revision"], original["revision"] + 1)
        self.assertEqual(updated["generation"], original["generation"])
        after = self.component.snapshot(self.project.data)
        self.assertNotEqual(before["generation"], after["generation"])
        self.assertEqual(before["documents"]["manual"]["graph"], after["documents"]["manual"]["graph"])
        result = await self.rag.asearch("Alice", method="bm25")
        self.assertEqual(result["documents"][0]["metadata"], {"tag": "new"})
        with self.assertRaises(RAGConflictError):
            await self.rag.aupdate_document("manual", title="stale", expected_revision=original["revision"],
                                            expected_generation=original["generation"])

    async def test_metadata_fast_path_does_not_hide_changed_preparation_or_invalid_title(self):
        await self.add()
        await self.configure(chunk_size=8)
        with patch.object(self.extractor, "extract", wraps=self.extractor.extract) as extract:
            await self.rag.aupdate_document("manual", description="updated")
        self.assertGreater(extract.await_count, 0)
        with self.assertRaises(ValueError):
            await self.rag.aupdate_document("manual", title="")

    async def test_extraction_reuses_only_identical_batches_and_preserves_provenance(self):
        await self.configure(extraction_batch_size=1)
        await self.add(content="Alice owns Atlas\n\nold paragraph")
        before = self.component.snapshot(self.project.data)["documents"]["manual"]
        with patch.object(self.extractor, "extract", wraps=self.extractor.extract) as extract:
            updated = await self.rag.aupdate_document("manual", content="Alice owns Atlas\n\nnew paragraph")
        self.assertEqual(extract.await_count, 1)
        self.assertEqual(extract.call_args.args[0][0]["text"], "new paragraph")
        self.assertEqual(updated["ingestion"]["extraction_batches_reused"], 1)
        after = self.component.snapshot(self.project.data)["documents"]["manual"]
        self.assertEqual(before["graph"]["relations"], after["graph"]["relations"])
        self.assertEqual(updated["ingestion"]["chunks_requested"], 1)

    async def test_extraction_cache_invalidates_on_model_params_and_source_ids(self):
        await self.configure(extraction_batch_size=1)
        self.extractor.params = {"model": "first"}
        await self.add(content="Alice owns Atlas\n\nother")
        self.extractor.params = {"model": "second"}
        with patch.object(self.extractor, "extract", wraps=self.extractor.extract) as extract:
            await self.rag.aupdate_document("manual", description="same body, new model")
        self.assertEqual(extract.await_count, 2)
        with patch.object(self.extractor, "extract", wraps=self.extractor.extract) as extract:
            await self.rag.aupdate_document("manual", content="prefix\n\nAlice owns Atlas\n\nother")
        self.assertEqual(extract.await_count, 3)
        after = self.component.snapshot(self.project.data)["documents"]["manual"]
        self.assertEqual(after["graph"]["relations"][0]["source_id"], "manual:c2")

    async def test_extraction_manifest_digest_rejects_changed_saved_evidence(self):
        await self.configure(extraction_batch_size=1)
        await self.add(content="Alice owns Atlas\n\nold")
        generation = self.component.corpus_version(self.project.data)[1]
        path = self.project.paths.root / "rag" / "generations" / generation / "corpus.json"
        saved = json.loads(path.read_text())
        saved["documents"]["manual"]["graph"]["relations"][0]["evidence"] = "Alice"
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            await self.rag.aupdate_document("manual", content="Alice owns Atlas\n\nnew")
        self.assertEqual(self.component.corpus_version(self.project.data)[1], generation)

    async def test_extraction_prompt_change_invalidates_reuse(self):
        from llm.components.rag import TripleExtractor
        calls = AsyncMock(return_value={"choices": [{"message": {"content": '{"entities": [], "relations": []}'}}]})
        extractor = TripleExtractor(model="test/extract", adapter=LiteLLMOperations(call=calls))
        self.component.extractor = extractor.with_extraction({}, {"messages": [{"role": "system", "content": "First"}]})
        await self.configure(extraction_batch_size=1)
        await self.add(content="one\n\ntwo")
        calls.reset_mock()
        self.component.extractor = extractor.with_extraction({}, {"messages": [{"role": "system", "content": "Second"}]})
        await self.rag.aupdate_document("manual", description="prompt changed")
        self.assertEqual(calls.await_count, 2)

    async def test_existing_job_graph_checkpoint_binding_is_unchanged(self):
        from llm.components.rag.splitting import split_markdown
        from llm.services.infrastructure.storage import revision_token
        await self.configure(extraction_batch_size=1)
        content = "Alice owns Atlas\n\nunavailable"
        job = await self.rag.aenqueue_document(identifier="manual", title="Manual", content=content)
        async def fail(chunks):
            if chunks[0]["text"] == "unavailable":
                raise ConnectionError("offline")
            return await Extractor().extract(chunks)
        with patch.object(self.extractor, "extract", fail):
            with self.assertRaises(ConnectionError):
                await self.rag.arun_job(job["id"])
        batch = split_markdown(content, "manual", chunk_size=2000)["chunks"][:1]
        expected = revision_token({"chunks": batch, "extractor": "Extractor", "params": {},
                                   "extraction": {"failure_policy": "required"}, "prompt": {"messages": []}})
        checkpoint = self.project.paths.root / "rag" / "jobs" / job["id"] / "batches" / "graph_0.json"
        self.assertEqual(json.loads(checkpoint.read_text())["signature"], expected)
        with patch.object(self.extractor, "extract", wraps=self.extractor.extract) as extract:
            await self.rag.arun_job(job["id"], retry=True)
        self.assertEqual(extract.await_count, 1)

    async def test_partial_extraction_retries_failed_batch_only(self):
        current = (await self.project.aget_data()).config.parameters["components"]["rag"]
        await self.rag.aconfigure(ProjectConfig.merge(current, {
            "config": {"extraction_batch_size": 1}, "policy": {"extraction": {"failure_policy": "best_effort"}}}))
        async def partial(chunks):
            if chunks[0]["text"] == "unavailable":
                raise ConnectionError("offline")
            return await Extractor().extract(chunks)
        with patch.object(self.extractor, "extract", partial):
            await self.add(content="Alice owns Atlas\n\nunavailable")
        with patch.object(self.extractor, "extract", wraps=self.extractor.extract) as extract:
            updated = await self.rag.aupdate_document("manual", description="retry missing extraction")
        self.assertEqual(extract.await_count, 1)
        self.assertTrue(updated["graph_complete"])

    async def test_snapshot_cache_reduces_reads_and_is_not_authoritative(self):
        await self.add()
        await self.configure(snapshot_cache_max_bytes=100_000)
        from llm.components.rag.component import read_json
        reads = []
        def read(path):
            if path.name == "corpus.json":
                reads.append(path)
            return read_json(path)
        with patch("llm.components.rag.component.read_json", side_effect=read):
            first = await self.rag.aget_document("manual")
            first["content"] = "caller mutation"
            second = await self.rag.aget_document("manual")
            await self.rag.asearch_documents("Alice", method="bm25")
        self.assertEqual(len(reads), 1)
        self.assertEqual(second["content"], "Alice owns Atlas")
        self.assertLessEqual(self.component.snapshot_cache.bytes, 100_000)
        await self.rag.aupdate_document("manual", content="new source")
        self.assertEqual((await self.rag.aget_document("manual"))["content"], "new source")
        generation = self.component.corpus_version(self.project.data)[1]
        source = self.project.paths.root / "rag" / "generations" / generation / "corpus.json"
        value = read_json(source)
        del value["documents"]["manual"]["generation"]
        source.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            await self.rag.aget_document("manual")
        source.unlink()
        with self.assertRaises(FileNotFoundError):
            await self.rag.aget_document("manual")

    async def test_snapshot_cache_is_disabled_without_capacity_and_with_small_capacity(self):
        await self.add()
        self.assertNotIn("snapshot_cache_max_bytes", self.component.resolve_config(self.project.data)["values"]["config"])
        from llm.components.rag.component import read_json
        for capacity in (None, 1, 0):
            if capacity is not None:
                await self.configure(snapshot_cache_max_bytes=capacity)
            with patch("llm.components.rag.component.read_json", wraps=read_json) as read:
                await self.rag.aget_document("manual")
                await self.rag.aget_document("manual")
            self.assertEqual(sum(call.args[0].name == "corpus.json" for call in read.call_args_list), 2)
            self.assertEqual(self.component.snapshot_cache.bytes, 0)

    def inputs(self):
        return [{"identifier": "one", "title": "First", "content": "batch one", "description": "first document"},
                {"identifier": "two", "title": "Second", "content": "batch two"}]

    async def test_bulk_add_publishes_once_and_keeps_catalog_and_indexes(self):
        await self.add()
        original = RAGComponent.publish
        publications = []
        def publish(component, project, previous, documents, **options):
            publications.append(set(documents))
            return original(component, project, previous, documents, **options)
        with patch.object(RAGComponent, "publish", publish):
            documents = await self.rag.aadd_documents(self.inputs())
        self.assertEqual(publications, [{"manual", "one", "two"}])
        self.assertEqual([doc["id"] for doc in documents], ["one", "two"])
        self.assertEqual(len((await self.rag.alist_catalog(limit=10))["documents"]), 3)
        self.assertTrue({"one", "two"}.issubset({hit["document_id"] for hit in await self.rag.asearch_documents("batch", method="bm25")}))
        self.assertTrue((await self.rag.agraph_search("Alice"))["relations"])

    async def test_bulk_input_errors_precede_provider_calls(self):
        for inputs in ([], self.inputs() + [self.inputs()[0]], [{"title": "x", "content": "y", "unknown": True}],
                       [self.inputs()[0], {"title": "second", "content": ""}]):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                await self.rag.aadd_documents(inputs)
        self.calls.assert_not_awaited()
        await self.add("one")
        self.calls.reset_mock()
        with self.assertRaises(FileExistsError):
            await self.rag.aadd_documents(self.inputs())
        self.calls.assert_not_awaited()

    async def test_bulk_failure_preserves_active_generation_and_never_exposes_first_document(self):
        await self.add()
        before = self.component.corpus_version(self.project.data)
        async def fail(**request):
            if request["input"] == ["batch two"]:
                raise ConnectionError("unavailable")
            return await fake_embedding(**request)
        self.calls.side_effect = fail
        with self.assertRaises(ProviderError):
            await self.rag.aadd_documents(self.inputs())
        self.assertEqual(self.component.corpus_version(self.project.data), before)
        self.assertEqual([doc["id"] for doc in await self.rag.alist_documents()], ["manual"])

    async def test_bulk_cancel_during_preparation_preserves_generation(self):
        await self.add()
        before = self.component.corpus_version(self.project.data)
        entered = asyncio.Event()
        async def block(**request):
            if request["input"] == ["batch two"]:
                entered.set()
                await asyncio.Event().wait()
            return await fake_embedding(**request)
        self.calls.side_effect = block
        task = asyncio.create_task(self.rag.aadd_documents(self.inputs()))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.component.corpus_version(self.project.data), before)

    async def test_bulk_job_resume_reuses_completed_documents_after_reopen(self):
        await self.add()
        before = self.component.corpus_version(self.project.data)
        job = await self.rag.aenqueue_documents(self.inputs())
        async def fail(**request):
            if request["input"] == ["batch two"]:
                raise ConnectionError("unavailable")
            return await fake_embedding(**request)
        self.calls.side_effect = fail
        with self.assertRaises(ProviderError):
            await self.rag.arun_job(job["id"])
        self.assertEqual(self.component.corpus_version(self.project.data), before)
        plan = await self.rag.acheckpoint_plan(job["id"])
        self.assertTrue(any("document_0" in key for key in plan.checkpoints))
        await self.app.shutdown()
        fresh_calls = AsyncMock(side_effect=fake_embedding)
        fresh_component = RAGComponent(embedding=EmbeddingModel(model="test/embedding", adapter=LiteLLMOperations(call=fresh_calls)), extractor=Extractor())
        async with LargeLanguageModel(Path(self.temp.name), engines={}, components=[fresh_component]) as reopened:
            project = await reopened.projects.aload(self.project.id)
            rag = await project.components.aget("rag")
            result = await rag.arun_job(job["id"], retry=True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual([call.kwargs["input"] for call in fresh_calls.call_args_list], [["batch two"]])
            self.assertEqual(result["ingestion"]["documents_completed"], 2)
            self.assertEqual(len(await rag.alist_documents()), 3)
            self.assertFalse((project.paths.root / "rag" / "jobs" / job["id"] / "batches").exists())

    async def test_bulk_job_receipt_failure_rolls_back_then_reuses_prepared_documents(self):
        from llm.components.rag.job_storage import atomic_json
        job = await self.rag.aenqueue_documents(self.inputs())
        before = self.component.corpus_version(self.project.data)
        def write(path, value):
            if path.name == "job.json" and value.get("status") == "completed":
                raise OSError("receipt unavailable")
            return atomic_json(path, value)
        with patch("llm.components.rag.job_storage.atomic_json", side_effect=write):
            with self.assertRaises(OSError):
                await self.rag.arun_job(job["id"])
        self.assertEqual(self.component.corpus_version(self.project.data), before)
        self.calls.reset_mock()
        result = await self.rag.arun_job(job["id"], retry=True)
        self.assertEqual(result["status"], "completed")
        self.calls.assert_not_awaited()
        self.assertNotEqual(self.component.corpus_version(self.project.data), before)

    async def test_bulk_job_matching_published_documents_are_not_published_again(self):
        from llm.components.rag.jobs import RAGJobs
        job = await self.rag.aenqueue_documents(self.inputs())
        with patch.object(RAGJobs, "_commit", side_effect=OSError("unrecorded completion")):
            with self.assertRaises(OSError):
                await self.rag.arun_job(job["id"])
        jobs = RAGJobs(self.rag)
        prepared = await self.rag._async_call(jobs._prepared, job["id"])
        # Exercise the persisted-state reconciliation guard independently of transaction rollback.
        _, snapshot = await self.rag._async_call(self.rag._snapshot)
        await self.rag._async_call(self.rag._commit_documents, snapshot, prepared["documents"])
        published = self.component.corpus_version(self.project.data)
        self.calls.reset_mock()
        with patch.object(RAGComponent, "publish", side_effect=AssertionError("already published")):
            await self.rag.arun_job(job["id"], retry=True)
        self.calls.assert_not_awaited()
        self.assertEqual(self.component.corpus_version(self.project.data), published)

    async def test_bulk_conflict_does_not_overwrite_concurrent_document(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def block(**request):
            if request["input"] == ["batch two"]:
                entered.set()
                await release.wait()
            return await fake_embedding(**request)
        self.calls.side_effect = block
        pending = asyncio.create_task(self.rag.aadd_documents(self.inputs()))
        await asyncio.wait_for(entered.wait(), 10)
        await self.add("concurrent")
        release.set()
        with self.assertRaises(RAGConflictError):
            await pending
        self.assertEqual([doc["id"] for doc in await self.rag.alist_documents()], ["concurrent"])

    async def test_bulk_cross_document_dimension_mismatch_never_publishes(self):
        async def dimensions(**request):
            return {"data": [{"embedding": [1., 2.] if request["input"] == ["batch one"] else [1., 2., 3.]}]}
        self.calls.side_effect = dimensions
        with self.assertRaises(ValueError):
            await self.rag.aadd_documents(self.inputs())
        self.assertEqual(await self.rag.alist_documents(), [])

    async def test_bulk_job_cancel_keeps_prepared_documents_but_requires_explicit_cleanup(self):
        job = await self.rag.aenqueue_documents(self.inputs())
        entered = asyncio.Event()
        async def block(**request):
            if request["input"] == ["batch two"]:
                entered.set()
                await asyncio.Event().wait()
            return await fake_embedding(**request)
        self.calls.side_effect = block
        task = asyncio.create_task(self.rag.arun_job(job["id"]))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self.rag.alist_documents(), [])
        plan = await self.rag.acheckpoint_plan(job["id"])
        self.assertTrue(any("document_0" in name for name in plan.checkpoints))
        with self.assertRaises(ValueError):
            await self.rag.arun_job(job["id"], retry=True)
        await self.rag.adelete_job(job["id"], expected_revision=plan.revision)

    async def test_bulk_job_abandon_delete_and_configuration_change_guard(self):
        from llm.components.rag.jobs import RAGJobs
        job = await self.rag.aenqueue_documents(self.inputs())
        with patch.object(RAGJobs, "_commit", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                await self.rag.arun_job(job["id"])
        await self.configure(chunk_size=10)
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            await self.rag.arun_job(job["id"], retry=True)
        plan = await self.rag.acheckpoint_plan(job["id"])
        await self.rag.aabandon_job(job["id"], expected_revision=plan.revision)
        with self.assertRaisesRegex(ValueError, "abandoned"):
            await self.rag.arun_job(job["id"], retry=True)
        plan = await self.rag.acheckpoint_plan(job["id"])
        await self.rag.adelete_job(job["id"], expected_revision=plan.revision)
        self.assertEqual(await self.rag.ajobs(), [])
