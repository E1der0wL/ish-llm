"""Run/RAG 공통 정리 계약: CAS, 승인 증거, 참조 보호, worker 경합과 원자적 삭제."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
import hashlib
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, AsyncMock

from llm.core.plans import CheckpointPlan
from llm.core.checkpoints import ResumeAbandonment
from llm.core.models import RunStatus
from llm.services.infrastructure.storage import atomic_json, read_json, revision_token
from llm.components.rag.jobs import RAGJobs
from llm.llm import LargeLanguageModel
from llm.components.workflows import WorkflowComponent
from tests.llm import test_conversation_turns as turn_fixtures, test_rag_components as rag_fixtures


class RunCheckpointTests(unittest.IsolatedAsyncioTestCase):
    setup_graph = turn_fixtures.TurnResumeTests.setup_graph
    resume = turn_fixtures.TurnResumeTests.resume

    async def test_approval_queries_read_checkpoint_and_receipt_once_per_operation(self):
        from llm.services.runtime import interactions
        run = await self.setup_graph()
        request, = await run.ainteractions()
        response = await run.arespond(request.respond(request.options[0].id))
        repository = self.app.run_repository
        for query in (run.ainteraction_views, run.ainteraction_responses,
                      lambda: run.ainteractions(pending_only=True)):
            with patch.object(repository, "checkpoint", wraps=repository.checkpoint) as checkpoint, \
                    patch.object(interactions, "read_json", wraps=interactions.read_json) as read:
                await query()
                self.assertEqual(checkpoint.call_count, 1)
                self.assertEqual(read.call_count, 1)
        # 다음 API 요청은 별도 스냅샷이다. 이미 읽은 응답도 재검증한다.
        path = run.data.paths.state / "interactions" / (request.id + ".json")
        atomic_json(path, {"run_id": run.id, "response": {**response.to_dict(), "request_fingerprint": "changed"}})
        with self.assertRaisesRegex(ValueError, "changed"):
            await run.ainteraction_views()

    async def test_checkpoint_plan_and_delete_share_reads_but_recheck_each_operation(self):
        from llm.services.runtime import interactions
        run = await self.setup_graph()
        request, = await run.ainteractions()
        await run.arespond(request.respond(request.options[0].id))
        await self.session.run.shutdown()
        repository = self.app.run_repository
        with patch.object(repository, "list", wraps=repository.list) as listed, \
                patch.object(repository, "checkpoint", wraps=repository.checkpoint) as checkpoint, \
                patch.object(interactions, "read_json", wraps=interactions.read_json) as read:
            plan = await run.acheckpoint_plan()
            self.assertEqual(listed.call_count, 1)
            self.assertEqual(checkpoint.call_count, 1)
            self.assertEqual(read.call_count, 1)
        await run.aabandon_resume(expected_revision=plan.revision)
        plan = await run.acheckpoint_plan()
        with patch.object(repository, "list", wraps=repository.list) as listed, \
                patch.object(repository, "checkpoint", wraps=repository.checkpoint) as checkpoint, \
                patch.object(interactions, "read_json", wraps=interactions.read_json) as read:
            await run.adelete_checkpoints(expected_revision=plan.revision)
            self.assertEqual(listed.call_count, 1)
            self.assertEqual(checkpoint.call_count, 1)
            self.assertEqual(read.call_count, 1)
        self.assertEqual(await run.ainteractions(), [request])
        self.assertFalse(self.effects)

    async def test_abandon_delete_preserves_conversation_steps_and_approval_after_reopen(self):
        run = await self.setup_graph()
        self.assertFalse((await run.acheckpoint_plan()).can_abandon)
        request, = await run.ainteractions()
        response = await run.arespond(request.respond(request.options[0].id))
        await self.session.run.shutdown()
        messages = [m.id for m in await self.session.aconversation()]
        steps = [s.id for s in await run.steps.alist()]
        checkpoint = await run.acheckpoint()
        plan = await run.acheckpoint_plan()
        self.assertEqual(CheckpointPlan.from_dict(plan.to_dict()), plan)
        self.assertTrue(plan.can_abandon)
        self.assertFalse(plan.can_delete)
        with self.assertRaisesRegex(ValueError, "Abandon"):
            await run.adelete_checkpoints(expected_revision=plan.revision)
        await run.aabandon_resume(expected_revision=plan.revision)
        self.assertEqual(await run.acheckpoint(), checkpoint)
        with self.assertRaisesRegex(ValueError, "checkpoint_conflict"):
            await run.adelete_checkpoints(expected_revision=plan.revision)
        plan = await run.acheckpoint_plan()
        self.assertTrue(plan.abandoned)
        self.assertTrue(plan.can_delete)
        self.assertGreater(plan.bytes, 0)
        await run.adelete_checkpoints(expected_revision=plan.revision)
        self.assertFalse((run.data.paths.state / "checkpoints").exists())
        self.assertEqual(await run.ainteractions(), [request])
        self.assertEqual(await run.ainteraction_responses(), [response])
        self.assertEqual([s.id for s in await run.steps.alist()], steps)
        self.assertEqual([m.id for m in await self.session.aconversation()], messages)
        self.assertFalse((await run.acheckpoint_plan()).can_delete)
        self.assertEqual(run.data.status, RunStatus.PAUSED)
        self.assertFalse(self.effects)
        project_id, session_id = self.project.id, self.session.id
        await self.app.shutdown()
        app = LargeLanguageModel(self.root, components=[WorkflowComponent()], engines={"graph": self.engine})
        self.addAsyncCleanup(app.shutdown)
        session = (await app.projects.aload(project_id)).sessions.load(session_id)
        saved = session.run.load(run.id)
        self.assertEqual(await saved.ainteractions(), [request])
        self.assertEqual(await saved.ainteraction_responses(), [response])
        self.assertTrue(all(v.status == "unavailable" for v in await saved.ainteraction_views()))
        for call in (saved.acheckpoint, lambda: saved.arespond(response),
                     lambda: session.run.resume(saved.id, engine="graph")):
            with self.assertRaises(ValueError):
                await call()
        self.assertFalse(self.effects)

    async def test_approval_change_invalidates_plan(self):
        run = await self.setup_graph()
        await self.session.run.shutdown()
        plan = await run.acheckpoint_plan()
        request, = await run.ainteractions()
        await run.arespond(request.respond(request.options[0].id))
        with self.assertRaisesRegex(ValueError, "checkpoint_conflict"):
            await run.aabandon_resume(expected_revision=plan.revision)
        self.assertNotIn("resume_abandonment", run.data.metadata)

    async def test_deletion_uses_repository_plan_and_detects_new_checkpoint_content(self):
        from llm.core.contracts import Diagnostic
        run = await self.setup_graph(pauses=())
        await self.session.run.shutdown()
        plan = await run.acheckpoint_plan()
        plan.delete_blockers.append(Diagnostic("maintenance_denied", "Repository maintenance denied"))
        with patch.object(self.app.run_repository, "checkpoint_plan", return_value=plan) as planned:
            with self.assertRaisesRegex(ValueError, "denied"):
                await run.adelete_checkpoints(expected_revision=plan.revision)
            planned.assert_called_once()
        plan = await run.acheckpoint_plan()
        # 구조적으로 유효한 JSON도 원본 내용이 바뀌면 삭제 CAS를 다시 확인해야 한다.
        path = run.data.paths.state / "checkpoints" / "graph" / "checkpoint.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "checkpoint_conflict"):
            await run.adelete_checkpoints(expected_revision=plan.revision)
        self.assertTrue(path.exists())

    async def test_child_lineage_protected_until_child_abandoned(self):
        first = await self.setup_graph(pauses=("first", "second"))
        second = await self.resume(first, "first")
        await self.session.run.shutdown()
        plan = await first.acheckpoint_plan()
        self.assertIn("resume_reference", [d.code for d in plan.abandon_blockers])
        with self.assertRaisesRegex(ValueError, "resume lineage"):
            await first.aabandon_resume(expected_revision=plan.revision)
        plan = await second.acheckpoint_plan()
        await second.aabandon_resume(expected_revision=plan.revision)
        plan = await first.acheckpoint_plan()
        await first.aabandon_resume(expected_revision=plan.revision)
        await first.adelete_checkpoints(expected_revision=(await first.acheckpoint_plan()).revision)
        self.assertTrue(await second.acheckpoint())
        await second.adelete_checkpoints(expected_revision=(await second.acheckpoint_plan()).revision)
        self.assertEqual(self.effects, ["first"])

    async def test_completed_deletion_and_transaction_rollback(self):
        run = await self.setup_graph(pauses=())
        await self.session.run.shutdown()
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        plan = await run.acheckpoint_plan()
        self.assertTrue(plan.can_delete)
        self.assertFalse(plan.can_abandon)
        checkpoint = await run.acheckpoint()
        with patch.object(self.app.run_repository, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await run.adelete_checkpoints(expected_revision=plan.revision)
        self.assertEqual(await run.acheckpoint(), checkpoint)
        self.assertNotIn("checkpoint_deletion", run.data.metadata)
        await run.adelete_checkpoints(expected_revision=(await run.acheckpoint_plan()).revision)
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        self.assertTrue(await run.steps.alist())

    async def test_corruption_and_symlinks_do_not_grant_deletion(self):
        run = await self.setup_graph()
        await self.session.run.shutdown()
        root = run.data.paths.state / "checkpoints"
        path = root / "graph" / "checkpoint.json"
        original = path.read_bytes()
        path.write_text('{}')
        with self.assertRaises((KeyError, ValueError)):
            await run.acheckpoint_plan()
        path.write_bytes(original)
        link = root / "unexpected"
        link.symlink_to(self.root)
        with self.assertRaises(ValueError):
            await run.acheckpoint_plan()
        link.unlink()

    async def test_failed_loop_cleanup_never_replays_provider_or_changes_messages(self):
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion
        provider = ScriptedCompletion([RuntimeError("offline")])
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[], engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}) as app:
                project = await app.projects.acreate(components=[], config={"parameters": {"engines": {
                    "loop": {"config": {"completion": {"model": "test/model"}}}}}})
                session = await project.sessions.acreate()
                run = await (await session.run.submit("fail", engine="loop")).wait()
                await session.run.shutdown()
                self.assertEqual(run.data.status, RunStatus.FAILED)
                before = await session.aconversation()
                await run.aabandon_resume(expected_revision=(await run.acheckpoint_plan()).revision)
                await run.adelete_checkpoints(expected_revision=(await run.acheckpoint_plan()).revision)
                with self.assertRaises(ValueError):
                    await session.run.resume(run.id, engine="loop")
                self.assertEqual(await session.aconversation(), before)
                self.assertEqual(len(provider.requests), 1)

    async def test_process_death_during_checkpoint_delete_rolls_back_on_reopen(self):
        run = await self.setup_graph(pauses=())
        await self.session.run.shutdown()
        checkpoint = await run.acheckpoint()
        project_id, session_id = self.project.id, self.session.id
        await self.app.shutdown()
        script = '''
import os, sys
from llm.llm import LargeLanguageModel
from llm.components.workflows import WorkflowComponent
app = LargeLanguageModel(sys.argv[1], components=[WorkflowComponent()], engines={})
session = app.projects.load(sys.argv[2]).sessions.load(sys.argv[3])
run = session.run.load(sys.argv[4])
plan = run.checkpoint_plan()
app.run_repository.save = lambda *args: os._exit(73)
run.delete_checkpoints(expected_revision=plan.revision)
'''
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-c", script, str(self.root), project_id, session_id, run.id],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr)
        app = LargeLanguageModel(self.root, components=[WorkflowComponent()], engines={})
        self.addAsyncCleanup(app.shutdown)
        saved = (await app.projects.aload(project_id)).sessions.load(session_id).run.load(run.id)
        self.assertEqual(await saved.acheckpoint(), checkpoint)
        self.assertNotIn("checkpoint_deletion", saved.data.metadata)
        await saved.adelete_checkpoints(expected_revision=(await saved.acheckpoint_plan()).revision)


class RAGCheckpointTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = rag_fixtures.RAGTests.asyncSetUp

    async def enqueue(self):
        return await self.rag.aenqueue_document(title="Manual", content="Alice owns Atlas", identifier="manual")

    def save_finished_chunk(self, jobs, identifier):
        # This lifecycle fixture represents a completed chunk; its stored receipt
        # must include the owning payload fields even when the provider is mocked.
        identity = {"fingerprint": revision_token({"fixture": "completed-embedding"}),
                    "text_hash": hashlib.sha256(b"Alice owns Atlas").hexdigest()}
        from llm.components.rag.splitting import split_markdown
        chunk_size = jobs.data._current()[1].chunk_size
        chunk = split_markdown("Alice owns Atlas", "manual", chunk_size=chunk_size)["chunks"][0]
        jobs._batch(identifier, "embedding_0", revision_token(identity),
                    {**identity, "vector": [1.0, 2.0], "chunk_size": chunk_size, "chunk": chunk})

    async def test_failed_job_abandon_delete_after_reopen(self):
        job = await self.enqueue()
        jobs = RAGJobs(self.rag)
        with patch.object(self.embed, "embed", AsyncMock(side_effect=ConnectionError("offline"))):
            with self.assertRaises(ConnectionError):
                await self.rag.arun_job(job["id"])
        self.save_finished_chunk(jobs, job["id"])
        plan = await self.rag.acheckpoint_plan(job["id"])
        self.assertEqual(CheckpointPlan.from_dict(plan.to_dict()), plan)
        self.assertTrue(plan.can_abandon)
        self.assertFalse(plan.can_delete)
        self.assertIn("batches/embedding_0.json", plan.checkpoints)
        await self.rag.aabandon_job(job["id"], expected_revision=plan.revision)
        self.assertEqual((await self.rag.ajob(job["id"]))["status"], "failed")
        with self.assertRaisesRegex(ValueError, "abandoned"):
            await self.rag.arun_job(job["id"], retry=True)
        with self.assertRaisesRegex(ValueError, "checkpoint_conflict"):
            await self.rag.adelete_job(job["id"], expected_revision=plan.revision)
        project_id = self.project.id
        await self.app.shutdown()
        app = LargeLanguageModel(self.root, engines={}, components=[self.rag_component])
        self.addAsyncCleanup(app.shutdown)
        rag = (await app.projects.aload(project_id)).components.rag
        with self.assertRaisesRegex(ValueError, "abandoned"):
            await rag.arun_job(job["id"], retry=True)
        plan = await rag.acheckpoint_plan(job["id"])
        await rag.adelete_job(job["id"], expected_revision=plan.revision)
        self.assertEqual(await rag.ajobs(), [])
        with self.assertRaises(FileNotFoundError):
            await rag.arun_job(job["id"], retry=True)

    async def test_completed_job_delete_preserves_published_corpus(self):
        job = await self.enqueue()
        await self.rag.arun_job(job["id"])
        doc = await self.rag.aget_document("manual")
        active = (self.project.paths.root / "rag" / "active.json").read_bytes()
        plan = await self.rag.acheckpoint_plan(job["id"])
        self.assertFalse(plan.can_abandon)
        self.assertTrue(plan.can_delete)
        await self.rag.adelete_job(job["id"], expected_revision=plan.revision)
        self.assertEqual(await self.rag.aget_document("manual"), doc)
        self.assertEqual((self.project.paths.root / "rag" / "active.json").read_bytes(), active)
        self.assertTrue(await self.rag.asearch("Alice", method="bm25"))

    async def test_live_lease_blocks_cancelled_job_deletion(self):
        job = await self.enqueue()
        jobs = RAGJobs(self.rag)
        lease = jobs._lease(job["id"])
        try:
            await self.rag.acancel_job(job["id"])
            plan = await self.rag.acheckpoint_plan(job["id"])
            self.assertFalse(plan.can_delete)
            self.assertIn("active_worker", [d.code for d in plan.delete_blockers])
            with self.assertRaisesRegex(ValueError, "still alive"):
                await self.rag.adelete_job(job["id"], expected_revision=plan.revision)
        finally:
            lease.close()
        plan = await self.rag.acheckpoint_plan(job["id"])
        await self.rag.adelete_job(job["id"], expected_revision=plan.revision)

    async def test_content_change_same_size_invalidates_revision_and_rollback(self):
        job = await self.enqueue()
        await self.rag.acancel_job(job["id"])
        plan = await self.rag.acheckpoint_plan(job["id"])
        root = self.project.paths.root / "rag" / "jobs" / job["id"]
        path = root / "input.json"
        path.write_text(path.read_text().replace("Atlas", "ATLAS"))
        with self.assertRaisesRegex(ValueError, "checkpoint_conflict"):
            await self.rag.adelete_job(job["id"], expected_revision=plan.revision)
        plan = await self.rag.acheckpoint_plan(job["id"])
        from llm.services.infrastructure.storage import remove_named_tree
        def fail(*args):
            remove_named_tree(*args)
            raise OSError("disk failure")
        with patch("llm.components.rag.job_storage.remove_named_tree", side_effect=fail):
            with self.assertRaises(OSError):
                await self.rag.adelete_job(job["id"], expected_revision=plan.revision)
        self.assertTrue(path.exists())
        await self.rag.adelete_job(job["id"], expected_revision=(await self.rag.acheckpoint_plan(job["id"])).revision)

    async def test_queued_abandon_is_not_automatically_executed(self):
        job = await self.enqueue()
        plan = await self.rag.acheckpoint_plan(job["id"])
        await self.rag.aabandon_job(job["id"], expected_revision=plan.revision)
        self.assertEqual(await self.rag.arun_jobs(), [])
        with self.assertRaisesRegex(ValueError, "abandoned"):
            await self.rag.arun_job(job["id"])

    async def test_cancelling_delete_waiter_does_not_leave_partial_storage(self):
        job = await self.enqueue()
        await self.rag.acancel_job(job["id"])
        plan = await self.rag.acheckpoint_plan(job["id"])
        entered, release = threading.Event(), threading.Event()
        from llm.services.infrastructure.storage import remove_named_tree
        def delayed(*args):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Test gate timed out")
            remove_named_tree(*args)
        with patch("llm.components.rag.job_storage.remove_named_tree", side_effect=delayed):
            operation = asyncio.create_task(self.rag.adelete_job(job["id"], expected_revision=plan.revision))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                operation.cancel()
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await operation
        self.assertEqual(await self.rag.ajobs(), [])

    async def test_cancel_preserves_finished_chunks_then_explicit_delete(self):
        jobs = RAGJobs(self.rag)
        job = await self.enqueue()
        entered, gate = asyncio.Event(), asyncio.Event()
        async def embed(*args, **kwargs):
            self.save_finished_chunk(jobs, job["id"])
            entered.set()
            await gate.wait()
        with patch.object(self.embed, "embed", embed):
            worker = asyncio.create_task(self.rag.arun_job(job["id"]))
            await asyncio.wait_for(entered.wait(), 5)
            plan = await self.rag.acheckpoint_plan(job["id"])
            self.assertFalse(plan.can_abandon)
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker
        plan = await self.rag.acheckpoint_plan(job["id"])
        self.assertIn("batches/embedding_0.json", plan.checkpoints)
        self.assertTrue(plan.can_delete)
        root = self.project.paths.root / "rag"
        self.assertEqual(read_json(root / "active.json"), {"generation": None})
        self.assertFalse((root / "generations").exists())
        self.assertEqual(await self.rag.alist_documents(), [])
        await self.rag.adelete_job(job["id"], expected_revision=plan.revision)

    async def test_stale_running_job_can_be_abandoned_without_replay(self):
        job = await self.enqueue()
        jobs = RAGJobs(self.rag)
        jobs._claim(job["id"], False)
        plan = await self.rag.acheckpoint_plan(job["id"])
        self.assertTrue(plan.can_abandon)  # OS lease, not a stale status/PID, owns execution.
        await self.rag.aabandon_job(job["id"], expected_revision=plan.revision)
        await self.rag.adelete_job(job["id"], expected_revision=(await self.rag.acheckpoint_plan(job["id"])).revision)

    async def test_abandoned_job_retention_is_explicit_and_live_lease_is_protected(self):
        with patch("llm.components.rag.jobs.now", return_value="2020-01-01T00:00:00+00:00"):
            job = await self.enqueue()
            await self.rag.aabandon_job(job["id"], expected_revision=(await self.rag.acheckpoint_plan(job["id"])).revision)
        jobs = RAGJobs(self.rag)
        from hub.backend.project.rag_management import RAGManagement
        manager = RAGManagement(self.rag, 'rag', self.project.paths.root, self.project.id, project=self.project)
        self.assertFalse((await manager.plan_job_retention(None))["selected"])
        lease = jobs._lease(job["id"])
        try:
            self.assertFalse((await self.rag.ajob_cleanup_plan())["candidates"])
        finally:
            lease.close()
        plan = await manager.plan_job_retention(60)
        await manager.apply_job_retention(60, plan)
        self.assertFalse(jobs._path(job["id"]).exists())


class ContractTests(unittest.TestCase):
    def test_abandonment_is_closed_and_has_no_authority_from_metadata(self):
        value = ResumeAbandonment("2026-10-16T00:00:00+00:00", "explicit")
        self.assertEqual(ResumeAbandonment.from_dict(value.to_dict()), value)
        for data in ({**value.to_dict(), "can_resume": True},
                     {**value.to_dict(), "reason": "maybe"},
                     {**value.to_dict(), "reason": "conversation_deleted"}):
            with self.assertRaises(ValueError):
                ResumeAbandonment.from_dict(data)
