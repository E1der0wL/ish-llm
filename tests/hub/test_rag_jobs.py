"""Hub RAG jobs preserve checkpoints across failures, crashes and popup lifetimes."""

import asyncio
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.project.rag_management import RAGManagement, RegistrationProgress
from hub.backend.runtime import HubRuntime
from hub.ui.application import create_application
from hub.widget.forms.rag_manager import RAGManager
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.core.contracts import Diagnostic
from tests.hub.support import SizedOutput, render_container
from tests.hub.test_components import config, Extractor


def settings(root, embed):
    return replace(config(root / "workspace"), file_root=root,
        component_factories=(lambda: RAGComponent(
            embedding=EmbeddingModel(model="test/embedding", embedding_fn=embed), extractor=Extractor()),))


async def wait_for(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(.02)


class RAGJobTests(unittest.IsolatedAsyncioTestCase):
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
                with self.assertRaisesRegex(ValueError, "cannot be resumed"):
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
                self.assertEqual(entries, [])
                self.assertEqual(await runtime.manage_records(project_id, "rag", "jobs"), [])
                job = updates[-1]
                self.assertEqual(job["status"], "failed")
                self.assertTrue(job["retryable"])
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
                self.assertEqual(entries, [])  # Hidden failures keep their backend checkpoints.
                entries = await runtime.manage_records(project_id, "rag", "retry", job["job_id"], progress=updates.append)
                self.assertEqual([item["name"] for item in entries], ["note"])
                self.assertEqual(calls, ["First chunk", "Second chunk", "Second chunk", "Third chunk"])
                self.assertEqual(updates[-1]["ingestion"]["checkpoint_reuse"], 1)
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
                with self.assertRaisesRegex(ValueError, "still alive"):
                    await observer.execute("retry", job["id"])
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
                view.composer.text = "/rag config"
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

    async def test_failed_job_disappears_and_does_not_block_reimport(self):
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
                view.composer.text = "/rag config"
                pipe.send_text("\r")
                await wait_for(lambda: isinstance(view._dialog, RAGManager) and not view._dialog.busy)
                popup = view._dialog
                popup.path.text = "retry.md"
                pipe.send_text("\r")
                await wait_for(lambda: not popup.busy and popup.last_job and popup.last_job["status"] == "failed")
                self.assertIsNone(popup._selected_job())
                self.assertEqual(popup.entries, [])
                # A failed row is hidden, not deleted from llm's storage. Import
                # the same path again without getting stuck on that hidden job.
                self.assertEqual(popup.path.text, "retry.md")
                pipe.send_text("\r")
                await wait_for(lambda: not popup.busy and popup.selected == "retry")
                self.assertEqual(calls, ["Saved chunk", "Retry chunk", "Saved chunk", "Retry chunk"])
                self.assertEqual(popup.last_job["ingestion"]["checkpoint_reuse"], 0)
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
                view.composer.text = "/rag config"
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
                    await wait_for(lambda: "0/1" in "\n".join(render_container(popup, width=100, height=32)))
                    rendered = "\n".join(render_container(popup, width=100, height=32))
                    self.assertIn("재시도", rendered)
                    self.assertIn("0/1", rendered)
                    view.close_dialog()
                view.composer.text = "/rag config"
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
        observer = RegistrationProgress(job, extraction=True)
        observer.observe(Diagnostic("rag_ingestion_progress", "embedding", details={"chunks_total": 4, "chunks_completed": 4, "embedding_seconds": 2}))
        self.assertEqual(observer.entry()["phase"], "extraction_index")
        observer.observe(Diagnostic("rag_extraction_progress", "extraction", details={"chunks_total": 4, "chunks_completed": 4, "extraction_batches": 1, "extraction_seconds": 3}))
        self.assertEqual(observer.entry()["phase"], "extraction_index")
        self.assertEqual(observer.entry()["ingestion"]["extraction_seconds"], 3)
