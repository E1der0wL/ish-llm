"""Public RAG/Vision APIs, safe cleanup and bounded completion context."""

from llm.core.model_protocol import ModelMessage

from llm.providers.operations import LiteLLMOperations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from hub.backend.runtime import HubRuntime
from hub.backend.project.record_management import DocumentManagement
from hub.backend.execution.rag_catalog import RAGCatalogSession
from llm.components.processing import CompletionMessage, CompletionRequest
from llm.components.vision import VisionComponent
from tests.hub.test_components import embedding
from tests.hub.test_rag_jobs import settings
from tests.hub.support import SizedOutput, render_container


class OCR:
    revision = "test-1"
    def describe_config(self):
        return {"type": "object"}

    async def recognize(self, image, *, options):
        return {"text": "RAG image guide", "blocks": [{"text": "guide", "box": [0, 0, 2, 2]}]}


class RAGExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_popup_buttons_fit_80_columns_and_image_choice_is_explicit(self):
        from prompt_toolkit.application import Application
        from prompt_toolkit.application.current import set_app
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.layout import Layout
        from hub.ui.view import HubView
        from hub.widget.forms.rag_manager import RAGManager
        from unittest.mock import Mock
        with create_pipe_input() as pipe:
            view = HubView()
            app = Application(layout=Layout(view.container), input=pipe, output=SizedOutput(columns=80, rows=30))
            request = Mock()
            popup = RAGManager(view, request, name="rag", edit_text=AsyncMock())
            with set_app(app):
                view.dialogs.show(popup, popup.path)
                text = "\n".join(render_container(popup, width=80, height=30))
                for key in ("settings_title", "history_retry", "rag_cancel_job", "settings_delete", "search_close"):
                    self.assertIn(view.t(key), text)
                popup.path.text = "screen.png"
                popup.import_path(popup.path.buffer)
                self.assertIsNot(view._dialog, popup)
                self.assertEqual(view._dialog.title, view.t("rag_image_title"))
                request.assert_not_called()
                # Closing a choice must not import or clear the selected path.
                view._dialog.on_close()
                self.assertIs(view._dialog, popup)
                self.assertEqual(popup.path.text, "screen.png")
                request.assert_not_called()
            await app.cancel_and_wait_for_background_tasks()

    async def test_failed_job_delete_is_revision_checked_and_preserves_documents(self):
        calls = []
        async def embed(**request):
            calls.append(request["input"])
            if request["input"] == ["Broken"]:
                raise RuntimeError("simulated failure")
            return await embedding(**request)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "broken.md").write_text("Broken")
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            try:
                data = await runtime.project.components.aget("rag")
                await data.aadd_document(identifier="keep", title="Keep", content="Keep this document")
                original = await data.aget_document("keep")
                async def manage(action, argument="", version=None):
                    return await runtime.manage_records(runtime.project.id, "rag", action, argument, version)
                with self.assertRaises(RuntimeError):
                    await manage("import", "broken.md")
                failed, = await manage("jobs")
                self.assertEqual(failed["status"], "failed")
                with self.assertRaisesRegex(ValueError, "unfinished"):
                    await manage("import", "broken.md")
                identifier = failed["job_id"]
                plan = await manage("job_delete_plan", identifier)
                with self.assertRaisesRegex(ValueError, "changed"):
                    await manage("delete_job", identifier, "stale-revision")
                await manage("delete_job", identifier, plan["revision"])
                with self.assertRaises(FileNotFoundError):
                    await data.ajob(identifier)
                self.assertEqual(await data.aget_document("keep"), original)
                self.assertEqual(await manage("jobs"), [])
                self.assertEqual(calls.count(["Broken"]), 1)
            finally:
                await runtime.close()

    async def test_cancel_inflight_job_and_delete_without_retrying(self):
        entered = asyncio.Event()
        async def embed(**request):
            entered.set()
            await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "active.md").write_text("Waiting")
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            task = None
            try:
                async def manage(action, argument="", version=None):
                    return await runtime.manage_records(runtime.project.id, "rag", action, argument, version)
                task = asyncio.create_task(manage("import", "active.md"))
                await asyncio.wait_for(entered.wait(), 10)
                row, = await manage("jobs")
                with self.assertRaises(ValueError):
                    await manage("delete_job", row["job_id"], "anything")
                entries = await asyncio.wait_for(manage("cancel_job", row["job_id"]), 10)
                self.assertTrue(task.cancelled())
                self.assertEqual(entries[0]["status"], "cancelled")
                self.assertFalse(entries[0]["retryable"])
                with self.assertRaises(ValueError):
                    await manage("retry", row["job_id"])
                plan = await manage("job_delete_plan", row["job_id"])
                self.assertEqual(await manage("delete_job", row["job_id"], plan["revision"]), [])
                self.assertFalse(runtime._rag_jobs)
            finally:
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await runtime.close()

    async def test_image_ocr_and_analysis_keep_provenance_and_use_rag_jobs(self):
        requests = []
        async def completion(**request):
            requests.append(request)
            return {"choices": [{"message": {"content": "A diagram of the network"}, "finish_reason": "stop"}]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("screen", "diagram", "unselected"):
                Image.new("RGB", (4, 4), "white").save(root / (name + ".png"))
            config = settings(root, embedding)
            config = replace(config, component_factories=(*config.component_factories,
                lambda: VisionComponent(ocr_backends={"test": OCR()}, adapter=LiteLLMOperations(call=completion))))
            config.project_config["parameters"]["components"]["vision"] = {
                "config": {"ocr": {"backend": "test"}, "completion": {"model": "test/vision"}}}
            runtime = HubRuntime(config)
            await runtime.start()
            try:
                async def manage(value):
                    return await runtime.manage_records(runtime.project.id, "rag", "import", value)
                with self.assertRaisesRegex(ValueError, "Choose OCR"):
                    await manage("unselected.png")
                vision = await runtime.project.components.aget("vision")
                self.assertEqual(await vision.alist(), {})
                for name, mode, kind in (("screen", "ocr", "ocr"), ("diagram", "analysis", "model_analysis")):
                    await manage({"path": name + ".png", "mode": mode})
                    data = await runtime.project.components.aget("rag")
                    doc = await data.aget_document(name)
                    self.assertEqual(doc["metadata"]["vision"]["kind"], kind)
                    self.assertEqual(doc["metadata"]["vision"]["source_kind"], "derived_text")
                    self.assertTrue(doc["description"])
                    self.assertNotIn("base64", json.dumps(doc))
                    self.assertEqual((await data.ajobs())[-1]["status"], "completed")
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["messages"][0]["content"][1]["type"], "image_url")
                with self.assertRaises(FileExistsError):
                    await manage({"path": "diagram.png", "mode": "analysis"})
                self.assertEqual(len(requests), 1)
                self.assertEqual(len(await vision.alist()), 2)
            finally:
                await runtime.close()

    async def test_empty_ocr_does_not_create_a_rag_job_or_leave_live_image(self):
        class EmptyOCR(OCR):
            async def recognize(self, image, *, options):
                return {"text": "", "blocks": []}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new("RGB", (4, 4), "white").save(root / "empty.png")
            config = settings(root, embedding)
            config = replace(config, component_factories=(*config.component_factories,
                lambda: VisionComponent(ocr_backends={"tesseract": EmptyOCR()})))
            runtime = HubRuntime(config)
            await runtime.start()
            try:
                with self.assertRaisesRegex(ValueError, "no document text"):
                    await runtime.manage_records(runtime.project.id, "rag", "import", {"path": "empty.png", "mode": "ocr"})
                self.assertEqual(await (await runtime.project.components.aget("rag")).ajobs(), [])
                self.assertEqual(await (await runtime.project.components.aget("vision")).alist(), {})
            finally:
                await runtime.close()

    async def test_explicit_vision_settings_enables_only_vision_in_existing_project(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = HubRuntime(settings(Path(folder), embedding))
            await runtime.start()
            try:
                project = runtime.project
                await project.components.aselect(["rag", "tools"])
                before = await project.aget_data()
                result = await runtime.manage_records(project.id, "rag", "vision_settings")
                after = await project.aget_data()
                self.assertEqual(result, {"component": "vision"})
                self.assertEqual(list(after.components), ["rag", "tools", "vision"])
                self.assertEqual(after.config.to_dict(), before.config.to_dict())
                self.assertEqual(after.title, before.title)
            finally:
                await runtime.close()

    async def test_catalog_manager_paginates_without_reading_document_bodies(self):
        data = AsyncMock()
        data.alist_catalog.side_effect = [
            {"documents": [{"id": "one", "title": "One", "description": "First", "generation": "a", "revision": 1}],
             "next_cursor": {"after": "one"}},
            {"documents": [{"id": "two", "title": "Two", "generation": "b", "revision": 2}], "next_cursor": None}]
        result = await DocumentManagement(data, "rag", Path(".")).list()
        self.assertEqual([item["name"] for item in result], ["one", "two"])
        data.alist_documents.assert_not_called()
        self.assertEqual(result[0]["description"], "First")
        self.assertEqual(data.alist_catalog.await_args.kwargs["cursor"], {"after": "one"})

    async def test_catalog_preview_bounded_cached_and_nonfatal_without_retrieval(self):
        data = AsyncMock()
        data.alist_catalog.return_value = {"documents": [{"id": "note", "title": "A" * 1000,
            "description": "B" * 2000}], "next_cursor": {"after": "note"}}
        session = RAGCatalogSession(data, True)
        def request():
            return CompletionRequest({}, [CompletionMessage(ModelMessage("user", "Question"), "u")], 1)
        for _ in range(2):
            current = request()
            async for _ in session.prepare(current):
                pass
            text = current.messages[0].message.text
            self.assertLess(len(text), 1600)
            self.assertIn('"more": true', text)
            self.assertIn("Skip retrieval", text)
            self.assertEqual(current.messages[-1].source_id, "u")
        data.alist_catalog.assert_awaited_once_with(limit=20)
        data.asearch.assert_not_called()
        data.alist_catalog.reset_mock()
        async for _ in RAGCatalogSession(data, False).prepare(request()):
            pass
        data.alist_catalog.assert_not_called()
        data.alist_catalog.side_effect = FileNotFoundError("catalog unavailable")
        with patch("hub.backend.execution.rag_catalog.diagnostic_failure") as report:
            current = request()
            async for _ in RAGCatalogSession(data, True).prepare(current):
                pass
            self.assertIn("unavailable", current.messages[0].message.text)
            report.assert_called_once()
