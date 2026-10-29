"""Hub RAG jobs preserve checkpoints across failures, crashes and popup lifetimes."""

from llm.providers.operations import LiteLLMOperations

import asyncio
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.project.rag_management import RAGManagement
from hub.backend.project.rag_progress import RegistrationProgress
from hub.backend.runtime import HubRuntime
from hub.ui.application import create_application
from hub.ui.operation_diagnostics import OperationDiagnostics
from hub.widget.forms.rag_manager import RAGManager
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef
from llm.llm import CheckpointError
from tests.hub.support import SizedOutput, render_container
from tests.hub.test_components import config, Extractor


def settings(root, embed):
    return replace(config(root / "workspace"), file_root=root,
        component_factories=(lambda: RAGComponent(
            embedding=EmbeddingModel(model="test/embedding", adapter=LiteLLMOperations(call=embed)), extractor=Extractor()),))


async def wait_for(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(.02)


class RAGJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_relation_failure_reuses_embeddings_and_completed_relation_batches(self):
        calls, graph_calls, fail = [], [], True

        async def embed(**request):
            calls.append(request['input'][0])
            return {'data': [{'embedding': [1., 2., 3.]}]}

        class FailingExtractor(Extractor):
            async def extract(self, chunks):
                text = chunks[0]['text']
                graph_calls.append(text)
                if fail and text == 'Second chunk':
                    raise RuntimeError('relation extraction unavailable')
                return await super().extract(chunks)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'relations.md'
            source.write_text('First chunk\n\nSecond chunk\n\nThird chunk')
            setup = replace(settings(root, embed), component_factories=(lambda: RAGComponent(
                embedding=EmbeddingModel(model='test/embedding', adapter=LiteLLMOperations(call=embed)),
                extractor=FailingExtractor()),))
            setup.project_config['parameters']['components']['rag']['config']['extraction_batch_size'] = 1
            runtime = HubRuntime(setup)
            try:
                await runtime.start()
                pid = runtime.project.id
                with self.assertRaisesRegex(RuntimeError, 'relation extraction'):
                    await runtime.manage_records(pid, 'rag', 'import', str(source))
                job, = await runtime.manage_records(pid, 'rag', 'list')
                self.assertTrue(job['retryable'])
            finally:
                await runtime.close()
            fail = False
            source.unlink()
            runtime = HubRuntime(setup)
            try:
                await runtime.start()
                updates = []
                rows = await runtime.manage_records(pid, 'rag', 'retry', job['job_id'], progress=updates.append)
                self.assertEqual([row['name'] for row in rows], ['relations'])
                self.assertEqual(calls, ['First chunk', 'Second chunk', 'Third chunk'])
                self.assertEqual(graph_calls, ['First chunk', 'Second chunk', 'Second chunk', 'Third chunk'])
                self.assertGreaterEqual(updates[0]['resume_checkpoints'], 4)
                self.assertEqual(updates[-1]['ingestion']['checkpoint_reuse'], 3)
            finally:
                await runtime.close()

    async def test_cancel_immediately_shows_stopping_and_does_not_duplicate_request(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from hub.locales import Language
        from hub.ui.operation_diagnostics import OperationDiagnostics

        popup = RAGManager.__new__(RAGManager)
        job = {"name": "job:j", "kind": "job", "job_id": "j", "status": "running", "phase": "embedding"}
        popup.entries, popup.selected, popup.cancel_pending = [job], "job:j", set()
        popup.view = SimpleNamespace(t=Language(), notice="")
        popup.request = Mock()
        app, timer, scheduler = Mock(), Mock(), Mock()
        scheduler.call_later.return_value = timer
        with patch("hub.widget.forms.rag_manager.get_app", return_value=app), \
                patch("hub.widget.forms.rag_manager.asyncio.get_running_loop", return_value=scheduler):
            popup.cancel_job()
            self.assertEqual(popup._phase(job), popup.view.t("rag_phase_cancelling"))
            scheduler.call_later.call_args.args[1]()
            self.assertEqual(popup.view.notice, popup.view.t("cancel_wait"))
            popup.cancel_job()
            popup.request.assert_called_once()
            stopped = {**job, "status": "cancelled", "phase": "cancelled"}
            popup.request.call_args.args[3]([stopped])
        timer.cancel.assert_called_once()
        self.assertFalse(popup.cancel_pending)
        self.assertEqual(popup.view.notice, "")
        self.assertEqual(popup.entries, [stopped])

    async def test_concurrent_resume_inspections_admit_only_one_local_observer(self):
        from types import SimpleNamespace
        ready, inspected = asyncio.Event(), 0
        async def inspect(identifier):
            nonlocal inspected
            inspected += 1
            if inspected == 2:
                ready.set()
            await ready.wait()
            return SimpleNamespace(can_resume=True, revision="r", blockers=[], checkpoints=[])
        async def execute(*args, **kwargs):
            await asyncio.sleep(.02)
        job = {"id": "j", "document_id": "d", "status": "failed", "phase": "failed", "created_at": "now",
               "resume_abandonment": None}
        data = SimpleNamespace(ajob=AsyncMock(return_value=job), aresume_plan=inspect,
            ajob_progress=AsyncMock(), arun_job=AsyncMock(side_effect=execute))
        manager = RAGManagement(data, "rag", Path("."), "project")
        results = await asyncio.gather(manager._run("j", progress=None), manager._run("j", progress=None),
                                       return_exceptions=True)
        self.assertEqual(sum(value is None for value in results), 1)
        self.assertEqual(sum(isinstance(value, ValueError) for value in results), 1)
        data.arun_job.assert_awaited_once()
        self.assertEqual(manager.active, {})

    async def test_poll_is_lightweight_and_retry_passes_reviewed_revision(self):
        from types import SimpleNamespace
        job = {"id": "j", "document_id": "d", "status": "failed", "phase": "failed", "created_at": "now",
               "resume_abandonment": None}
        plan = SimpleNamespace(can_resume=True, revision="reviewed", blockers=[], checkpoints=[])
        data = SimpleNamespace(ajobs=AsyncMock(return_value=[job]), ajob=AsyncMock(return_value=job),
            aresume_plan=AsyncMock(return_value=plan), ajob_progress=AsyncMock(),
            arun_job=AsyncMock(side_effect=CheckpointError("checkpoint_conflict", "changed")))
        manager = RAGManagement(data, "rag", Path("."), "project")
        entries = await manager.execute("jobs")
        self.assertIsNone(entries[0]["retryable"])
        data.aresume_plan.assert_not_awaited()
        self.assertTrue((await manager.jobs())[0]["retryable"])
        with self.assertRaises(CheckpointError):
            await manager.execute("retry", "j")
        data.arun_job.assert_awaited_once_with("j", retry=True, expected_revision="reviewed")
        self.assertEqual(manager.active, {})

    async def test_batch_job_lists_retries_and_blocks_duplicate_document_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "first.md"
            source.write_text("First")
            calls, updates = [], []
            async def embed(**request):
                calls.append(request["input"])
                return {"data": [{"embedding": [1., 2., 3.]}]}
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            try:
                data = await runtime.project.components.aget("rag")
                job = await data.aenqueue_documents([
                    {"identifier": "first", "title": "First", "content": "First"},
                    {"identifier": "second", "title": "Second", "content": "Second"}])
                rows = await runtime.manage_records(runtime.project.id, "rag", "list")
                self.assertEqual(rows[0]["label"], "first, second")
                self.assertTrue(rows[0]["retryable"])
                with self.assertRaisesRegex(ValueError, "unfinished RAG job"):
                    await runtime.manage_records(runtime.project.id, "rag", "import", str(source))
                self.assertEqual(calls, [])
                rows = await runtime.manage_records(runtime.project.id, "rag", "retry", job["id"], progress=updates.append)
                self.assertEqual({row["name"] for row in rows}, {"first", "second"})
                self.assertEqual(calls, [["First"], ["Second"]])
                self.assertEqual(updates[-1]["status"], "completed")
            finally:
                await runtime.close()

    async def test_abandoned_job_cannot_retry_or_block_explicit_new_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "note.md"
            source.write_text("New source")
            calls = []
            async def embed(**request):
                calls.append(request["input"])
                return {"data": [{"embedding": [1., 2., 3.]}]}
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            try:
                data = await runtime.project.components.aget("rag")
                job = await data.aenqueue_document(identifier="note", title="Old", content="Old source")
                plan = await data.acheckpoint_plan(job["id"])
                await data.aabandon_job(job["id"], expected_revision=plan.revision)
                rows = await runtime.manage_records(runtime.project.id, "rag", "jobs")
                self.assertFalse(rows[0]["retryable"])
                with self.assertRaisesRegex(ValueError, "abandoned"):
                    await runtime.manage_records(runtime.project.id, "rag", "retry", job["id"])
                await runtime.manage_records(runtime.project.id, "rag", "import", str(source))
                self.assertEqual(calls, [["New source"]])
            finally:
                await runtime.close()

    async def test_failed_registration_reopens_and_reuses_completed_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "note.md"
            source.write_text("First chunk\n\nSecond chunk\n\nThird chunk")
            calls, fail, updates = [], True, []
            async def embed(**request):
                text = request["input"][0]
                calls.append(text)
                if fail and text == "Second chunk":
                    raise RuntimeError("simulated embedding failure")
                return {"data": [{"embedding": [1., 2., 3.]}]}
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            project_id = runtime.project.id
            try:
                with self.assertRaises(RuntimeError):
                    await runtime.manage_records(project_id, "rag", "import", str(source), progress=updates.append)
                entries = await runtime.manage_records(project_id, "rag", "list")
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["status"], "failed")
                job = updates[-1]
                self.assertEqual(job["status"], "failed")
                self.assertIsNone(job["retryable"])  # Progress does not perform checkpoint inspection.
                self.assertTrue(entries[0]["retryable"])
                self.assertEqual(job["ingestion"]["chunks_completed"], 1)
                self.assertEqual(updates[-1]["phase"], "failed")
            finally:
                await runtime.close()
            fail = False
            source.unlink()  # Resume uses the backend's durable input, not a new file read.
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            try:
                entries = await runtime.manage_records(project_id, "rag", "list")
                self.assertEqual(entries[0]["job_id"], job["job_id"])
                updates.clear()
                entries = await runtime.manage_records(project_id, "rag", "retry", job["job_id"], progress=updates.append)
                self.assertEqual([item["name"] for item in entries], ["note"])
                self.assertEqual(calls, ["First chunk", "Second chunk", "Second chunk", "Third chunk"])
                self.assertEqual(updates[-1]["ingestion"]["checkpoint_reuse"], 1)
                self.assertGreater(updates[0]['resume_checkpoints'], 0)
                self.assertEqual(updates[0]['phase'], 'resuming')
                self.assertTrue(all(item['phase'] != 'failed' for item in updates))
                self.assertEqual(updates[-1]["phase"], "completed")
                self.assertEqual(updates[-1]["description"], "")
                self.assertIsNotNone(updates[-1]["elapsed"])
                data = await runtime.project.components.aget("rag")
                self.assertEqual((await data.ajob(job["job_id"]))["status"], "completed")
                self.assertEqual(len(await data.ajobs()), 1)
                with self.assertRaises(ValueError):
                    await runtime.manage_records(project_id, "rag", "retry", job["job_id"])
            finally:
                await runtime.close()

    async def test_cancelled_jobs_and_active_jobs_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []
            async def embed(**request):
                calls.append(request["input"])
                entered.set()
                await release.wait()
                return {"data": [{"embedding": [1., 2., 3.]}]}
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            task = None
            try:
                data = await runtime.project.components.aget("rag")
                cancelled = await data.aenqueue_document(title="Cancelled", content="cancel", identifier="cancel")
                await data.acancel_job(cancelled["id"])
                with self.assertRaises(ValueError):
                    await runtime.manage_records(runtime.project.id, "rag", "retry", cancelled["id"])
                job = await data.aenqueue_document(title="Pending", content="active", identifier="active")
                task = asyncio.create_task(runtime.manage_records(runtime.project.id, "rag", "retry", job["id"]))
                await asyncio.wait_for(entered.wait(), 10)
                entries = await runtime.manage_records(runtime.project.id, "rag", "jobs")
                active = next(item for item in entries if item["job_id"] == job["id"])
                self.assertTrue(active["live"])
                self.assertFalse(active["retryable"])
                self.assertFalse(next(item for item in entries if item["job_id"] == cancelled["id"])["retryable"])
                with self.assertRaises(ValueError):
                    await runtime.manage_records(runtime.project.id, "rag", "retry", job["id"])
                # An observer without Hub's in-memory map still cannot bypass the backend lease.
                observer = RAGManagement(data, "rag", root, runtime.project.id)
                with self.assertRaises(CheckpointError) as caught:
                    await observer.execute("retry", job["id"])
                self.assertTrue(caught.exception.blockers)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                if task:
                    await task
                await runtime.close()

    async def test_abrupt_process_exit_leaves_resumable_running_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "crash.md"
            source.write_text("First chunk\n\nSecond chunk")
            script = '''
import asyncio, os, sys
from pathlib import Path
from tests.hub.test_rag_jobs import settings
from hub.backend.runtime import HubRuntime
async def embed(**request):
    if request["input"] == ["Second chunk"]:
        os._exit(23)
    return {"data": [{"embedding": [1., 2., 3.]}]}
async def main():
    root = Path(sys.argv[1])
    runtime = HubRuntime(settings(root, embed))
    await runtime.start()
    await runtime.manage_records(runtime.project.id, "rag", "import", str(root / "crash.md"))
asyncio.run(main())
'''
            result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, str(root)],
                                             capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 23, result.stderr)
            calls = []
            async def embed(**request):
                calls.append(request["input"])
                return {"data": [{"embedding": [1., 2., 3.]}]}
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            try:
                entries = await runtime.manage_records(runtime.project.id, "rag", "list")
                self.assertEqual(entries[0]["phase"], "unconfirmed")
                self.assertTrue(entries[0]["retryable"])
                await runtime.manage_records(runtime.project.id, "rag", "retry", entries[0]["job_id"])
                self.assertEqual(calls, [["Second chunk"]])
            finally:
                await runtime.close()


class RAGJobUITests(unittest.IsolatedAsyncioTestCase):
    def test_blocked_resume_shows_owner_reason_without_running(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from hub.locales import Language
        popup = RAGManager.__new__(RAGManager)
        popup.busy, popup.selected = False, "job:j"
        popup.entries = [{"name": "job:j", "kind": "job", "retryable": False,
                          "resume_blockers": ({"code": "checkpoint_configuration", "message": "The indexing settings changed"},)}]
        popup.view = SimpleNamespace(t=Language(), notice="", project_id="p", operation_diagnostics=OperationDiagnostics())
        popup.call = Mock()
        with patch("hub.widget.forms.rag_manager.get_app", return_value=Mock()):
            popup.retry()
        self.assertIn("RAG 설정", popup.view.notice)
        self.assertIn("The indexing settings changed", popup.view.operation_diagnostics.text("p", popup.view.t))
        popup.call.assert_not_called()

    async def test_retry_button_runs_persisted_queued_job(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            calls = []
            async def embed(**request):
                calls.append(request["input"])
                return {"data": [{"embedding": [1., 2., 3.]}]}
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            try:
                data = await runtime.project.components.aget("rag")
                job = await data.aenqueue_document(title="Queued", content="Saved input", identifier="queued")
            finally:
                await runtime.close()
            app, controller = create_application(settings(root, embed), input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await wait_for(lambda: view.connected)
                view.composer.text = "/rag"
                pipe.send_text("\r")
                await wait_for(lambda: isinstance(view._dialog, RAGManager) and not view._dialog.busy)
                popup = view._dialog
                self.assertEqual(popup._selected_job()["job_id"], job["id"])
                with set_app(app):
                    app.layout.focus(popup.retry_button)
                pipe.send_text("\r")
                await wait_for(lambda: not popup.busy and popup.selected == "queued")
                self.assertEqual(calls, [["Saved input"]])
                self.assertEqual([item["name"] for item in popup.entries], ["queued"])
                self.assertFalse(view.progress.active)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_failed_job_remains_and_retry_reuses_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            (root / "retry.md").write_text("Saved chunk\n\nRetry chunk")
            calls = []
            async def embed(**request):
                text = request["input"][0]
                calls.append(text)
                if text == "Retry chunk" and calls.count(text) == 1:
                    raise RuntimeError("temporary test failure")
                return {"data": [{"embedding": [1., 2., 3.]}]}
            app, controller = create_application(settings(root, embed), input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await wait_for(lambda: view.connected)
                view.composer.text = "/rag"
                pipe.send_text("\r")
                await wait_for(lambda: isinstance(view._dialog, RAGManager) and not view._dialog.busy)
                popup = view._dialog
                popup.path.text = "retry.md"
                pipe.send_text("\r")
                await wait_for(lambda: not popup.busy and popup.last_job and popup.last_job["status"] == "failed")
                self.assertIsNot(popup._selected_job()["retryable"], False)
                self.assertEqual(len(popup.entries), 1)
                self.assertEqual(popup.path.text, "retry.md")
                with set_app(app):
                    app.layout.focus(popup.retry_button)
                pipe.send_text("\r")
                await wait_for(lambda: not popup.busy and popup.selected == "retry")
                self.assertEqual(calls, ["Saved chunk", "Retry chunk", "Retry chunk"])
                self.assertEqual(popup.last_job["ingestion"]["checkpoint_reuse"], 1)
                self.assertFalse(view.progress.active)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_popup_close_reopen_tracks_job_and_does_not_cancel_registration(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            (root / "note.md").write_text("Long running chunk")
            entered, release = threading.Event(), threading.Event()
            async def embed(**request):
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(.02)
                return {"data": [{"embedding": [1., 2., 3.]}]}
            app, controller = create_application(settings(root, embed), input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await wait_for(lambda: view.connected)
                view.composer.text = "/rag"
                pipe.send_text("\r")
                await wait_for(lambda: isinstance(view._dialog, RAGManager) and not view._dialog.busy)
                popup = view._dialog
                popup.path.text = "note.md"
                pipe.send_text("\r")
                await wait_for(lambda: entered.is_set() and popup.last_job and popup.last_job["phase"] == "embedding")
                self.assertTrue(popup.busy)
                self.assertTrue(view.progress.active)
                with set_app(app):
                    # PTK caches formatted controls for each rendered frame;
                    # a backend callback may arrive before the next frame.
                    size = app.output.get_size()
                    def paint_popup():
                        return "\n".join(render_container(popup, width=size.columns, height=size.rows))
                    await wait_for(lambda: "0/1" in paint_popup())
                    rendered = paint_popup()
                    self.assertIn("재시도", rendered)
                    self.assertIn("0/1", rendered)
                    view.close_dialog()
                view.composer.text = "/rag"
                pipe.send_text("\r")
                await wait_for(lambda: isinstance(view._dialog, RAGManager) and view._dialog is not popup and not view._dialog.busy)
                reopened = view._dialog
                self.assertTrue(reopened.entries[0]["live"])
                with set_app(app):
                    reopened.retry()
                self.assertIn("미완료", view.notice)
                release.set()
                await wait_for(lambda: not view.progress.active and any(item["name"] == "note" for item in reopened.entries))
                self.assertFalse(any(item.get("kind") == "job" for item in reopened.entries))
                with set_app(app):
                    view.close_dialog()
                self.assertIsNone(reopened._poll_timer)
            finally:
                release.set()
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    def test_phase_progress_does_not_claim_graph_batches_are_overall_percent(self):
        job = {"id": "job", "document_id": "doc", "status": "running", "phase": "preparing", "created_at": "2026-01-01T00:00:00+00:00"}
        observer = RegistrationProgress(job)
        observer.observe(Diagnostic("rag_ingestion_progress", "embedding", details={"chunks_total": 4, "chunks_completed": 4, "embedding_seconds": 2}))
        self.assertEqual(observer.entry()["phase"], "embedding")
        observer.update(OperationProgress(ResourceRef("rag_job", "job"), "extracting", completed=1, total=3, unit="batch"))
        observer.observe(Diagnostic("rag_extraction_progress", "extraction", details={"chunks_total": 4, "chunks_completed": 4, "extraction_batches": 1, "extraction_seconds": 3}))
        self.assertEqual(observer.entry()["phase"], "extracting")
        self.assertEqual(observer.entry()["ingestion"]["extraction_seconds"], 3)
        self.assertEqual(observer.entry()["progress"]["total"], 3)
        observer.update(OperationProgress(ResourceRef("rag_job", "job"), "publishing"))
        self.assertIsNone(observer.entry()["progress"]["total"])
        self.assertIn("extracting", observer.entry()["phase_seconds"])
