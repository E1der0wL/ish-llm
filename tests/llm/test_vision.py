"""Vision의 무효과 준비/저장 경계, 실제 OCR, provider 관찰과 Loop/RAG 연결을 검증한다."""

import asyncio
import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw, ImageFont

from llm.components.vision import VisionComponent, TesseractBackend, VisionError
from llm.components.vision import images
from llm.components.vision.backends import validate_ocr
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.core.models import ProjectConfig, RunStatus, StepStatus
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.graph.tool import ToolNode
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.llm import LargeLanguageModel, ServiceConfig
from llm.services.runtime.tools import ToolPolicy, ToolApprovalRequired
from tests.llm.configuration_fixtures import rag_settings
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm.test_rag_components import fake_embedding


def picture(size=(80, 50), *, format="PNG"):
    with Image.new("RGB", size, "white") as image:
        output = BytesIO()
        image.save(output, format=format)
        return output.getvalue()


def response(text="Visible error: connection refused"):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}}


class OCR:
    revision = "fixture-v1"

    def __init__(self):
        self.calls = []

    def configuration_schema(self):
        return {"type": "object", "properties": {"language": {"type": "string"}}, "additionalProperties": False}

    async def recognize(self, image, *, options):
        self.calls.append((image, options))
        return {"text": "Connection refused. Check server status.", "blocks": [
            {"text": "Connection", "box": [0, 0, 10, 10], "confidence": 92}], "confidence_kind": "fixture"}


class ImageTests(unittest.TestCase):
    def test_formats_damage_and_limits(self):
        for format in ("PNG", "JPEG"):
            self.assertEqual(images.inspect_image(picture(format=format), {})["width"], 80)
        for raw in (b"", b"not an image", picture()[:40]):
            with self.subTest(raw=raw[:10]), self.assertRaises(Exception):
                images.inspect_image(raw, {})
        with self.assertRaisesRegex(ValueError, "max_bytes"):
            images.inspect_image(picture(), {"max_bytes": 1})
        with self.assertRaisesRegex(ValueError, "max_pixels"):
            images.inspect_image(picture(), {"max_pixels": 10})
        with Image.new("RGB", (2, 2)) as frame:
            output = BytesIO()
            frame.save(output, format="GIF")
        with self.assertRaisesRegex(ValueError, "PNG/JPEG"):
            images.inspect_image(output.getvalue(), {})

    def test_preprocessing_order_crop_and_format(self):
        raw = picture()
        ops = [{"operation": "crop", "box": [10, 10, 40, 30]}, {"operation": "resize", "scale": 2},
               {"operation": "rotate", "degrees": 90}, {"operation": "grayscale"}]
        prepared, meta = images.preprocess(raw, ops, {})
        self.assertEqual((meta["width"], meta["height"]), (40, 60))
        self.assertEqual(images.inspect_image(raw, {})["width"], 80)
        _, jpg = images.preprocess(prepared, [{"operation": "format", "format": "JPEG"}], {})
        self.assertEqual(jpg["mime_type"], "image/jpeg")
        for ops in ([{"operation": "crop", "box": [1, 2, 1000, 5]}],
                    [{"operation": "resize", "scale": float("nan")}],
                    [{"operation": "format", "format": "JPEG"}, {"operation": "grayscale"}]):
            with self.subTest(ops=ops), self.assertRaises(ValueError):
                images.preprocess(raw, ops, {})

    def test_ocr_results_validate_geometry_and_finiteness(self):
        for result in ({"text": "x", "blocks": [{"text": "x", "box": [0, 0, 100, 1]}]},
                       {"text": "x", "blocks": [{"text": "x", "box": [0, 0, 1, 1], "confidence": float("nan")}]},
                       {"text": None, "blocks": []}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                validate_ocr(result, 10, 10)


class VisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ocr = OCR()
        self.complete = AsyncMock(return_value=response())
        self.component = VisionComponent(ocr_backends={"first": self.ocr, "second": OCR()}, completion_fn=self.complete)
        self.app = await self.enterAsyncContext(LargeLanguageModel(self.root, components=[self.component]))
        self.project = await self.app.projects.acreate("Vision", components=["vision"])
        self.vision = self.project.components.vision
        self.image = await self.vision.aimport_image(picture(), title="Probe", identifier="probe")

    async def test_missing_explicit_selection_overrides_and_schema(self):
        self.assertEqual((await self.vision.aeffective_configuration())["values"], {})
        with self.assertRaisesRegex(ValueError, "vision.ocr.backend"):
            await self.vision.aocr("probe")
        await self.vision.aconfigure({"ocr": {"backend": "first", "backends": {"first": {"language": "eng"}}}})
        result = await self.vision.aocr("probe")
        self.assertEqual((result["backend"], self.ocr.calls[-1][1]), ("first", {"language": "eng"}))
        self.assertEqual((await self.vision.aocr("probe", backend="second"))["backend"], "second")
        self.assertEqual(len(self.ocr.calls), 1)
        for backend in (None, "auto", "missing"):
            with self.subTest(backend=backend), self.assertRaises(ValueError):
                await self.vision.aocr("probe", backend=backend)
        for config in ({"ocr": {"backend": "auto"}}, {"ocr": {"backend": None}}, {"ocr": {"backends": {"unknown": {}}}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                await self.vision.aconfigure(config)

    async def test_assets_immutable_tombstones_and_clone(self):
        prepared = await self.vision.apreprocess("probe", operations=[{"operation": "grayscale"}])
        await self.vision.aupdate("probe", {"title": "New title"})
        with self.assertRaisesRegex(ValueError, "immutable"):
            await self.vision.aupdate("probe", {"sha256": "0" * 64})
        await self.vision.adelete("probe")
        self.assertNotIn("probe", await self.vision.alist())
        with self.assertRaises(FileExistsError):
            await self.vision.aimport_image(picture(), title="reuse", identifier="probe")
        self.assertEqual((await self.vision.aload(prepared["id"]))["origin"]["sha256"], self.image["sha256"])
        clone = await self.project.aclone(title="Copy")
        self.assertEqual(await clone.components.vision.alist(), await self.vision.alist())
        self.component.validate_backup(clone.data)
        with self.assertRaises(FileNotFoundError):
            await clone.components.vision.aload("probe")

    async def test_commit_failure_rolls_back_blob_and_record(self):
        before = sorted(p.name for p in (self.project.paths.root / "vision/assets").iterdir())
        with patch("llm.components.base.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.vision.aimport_image(picture((2, 2)), title="failed", identifier="failed")
        self.assertEqual(sorted(p.name for p in (self.project.paths.root / "vision/assets").iterdir()), before)
        self.assertNotIn("failed", await self.vision.alist())

    async def test_corrupt_asset_and_cross_project_are_rejected(self):
        other = await self.app.projects.acreate("Other", components=["vision"])
        with self.assertRaises(FileNotFoundError):
            await other.components.vision.aocr("probe", backend="first")
        asset = self.project.paths.root / "vision/assets" / (self.image["sha256"] + ".bin")
        asset.write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "integrity"):
            await self.vision.aocr("probe", backend="first")
        self.assertEqual(self.ocr.calls, [])

    async def test_analysis_uses_provider_and_no_hidden_request_parameters(self):
        with self.assertRaisesRegex(ValueError, "completion.model"):
            await self.vision.aanalyze("probe", prompt="What is visible?")
        await self.vision.aconfigure({"completion": {"model": "test/vision", "max_retries": 3}})
        result = await self.vision.aanalyze("probe", prompt="What is visible?")
        self.assertEqual(result["kind"], "model_analysis")
        params = self.complete.call_args.kwargs
        self.assertFalse(params["stream"])
        self.assertEqual(params["max_retries"], 3)
        self.assertFalse(set(params) & {"timeout", "temperature", "max_tokens", "num_retries"})
        url = params["messages"][0]["content"][1]["image_url"]["url"]
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), picture())
        self.assertNotIn("base64", json.dumps(result))
        usage = await self.vision.amodel_usage()
        self.assertEqual(usage[-1]["usage"]["total_tokens"], 10)
        self.assertNotIn("base64", json.dumps(usage))
        self.assertEqual((await self.vision.acompletion_content("probe"))["image_url"]["url"], url)

    async def test_ocr_cancellation_and_explicit_timeout(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def slow(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.ocr.recognize = slow
        task = asyncio.create_task(self.vision.aocr("probe", backend="first"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        await self.vision.aconfigure({"ocr": {"backend": "first", "timeout": 0.01}})
        with self.assertRaises(TimeoutError):
            await self.vision.aocr("probe")
        await self.vision.aconfigure({"ocr": {"backend": "first", "timeout": None}})
        self.assertIsNone((await self.vision.aeffective_configuration())["values"]["ocr"]["timeout"])

    async def test_configuration_change_and_deletion_during_ocr_fail_closed(self):
        for change in ("config", "delete"):
            started, release = asyncio.Event(), asyncio.Event()
            original = OCR().recognize
            async def delayed(raw, *, options):
                started.set()
                await release.wait()
                return await original(raw, options=options)
            self.ocr.recognize = delayed
            task = asyncio.create_task(self.vision.aocr("probe", backend="first"))
            await started.wait()
            if change == "config":
                await self.vision.aconfigure({"ocr": {"backend": "second"}})
            else:
                await self.vision.adelete("probe")
            release.set()
            with self.assertRaises((ValueError, FileNotFoundError)):
                await task

    async def test_tools_validate_schema_and_bind_configuration(self):
        from llm.components.vision.tools import vision_tools
        tools = vision_tools(self.vision, self.component.binding(self.project.data), ("first", "second"))
        with self.assertRaises(ValueError):
            tools.prepare("image_ocr", '{"image_id":"probe"}')
        await self.vision.aconfigure({"ocr": {"backend": "first"}})
        with self.assertRaisesRegex(ValueError, "changed"):
            await tools.get("image_ocr").handler({"image_id": "probe", "backend": "first"})

    async def test_extract_document_marks_derived_source_and_keeps_regions(self):
        document = await self.vision.aextract_document("probe", mode="ocr", title="OCR", backend="first")
        self.assertEqual(document["metadata"]["vision"]["source_kind"], "derived_text")
        self.assertEqual(document["metadata"]["vision"]["image"]["sha256"], self.image["sha256"])
        self.assertTrue(document["metadata"]["vision"]["blocks"])
        self.assertNotIn("base64", json.dumps(document))

    async def test_provider_retry_contract_cancellation_and_code(self):
        from llm.providers.requests import ProviderError
        class RateLimit(Exception):
            status_code = 429
        await self.vision.aconfigure({"completion": {"model": "test", "max_retries": 2},
                                     "provider": {"max_attempts": 3}})
        self.complete.side_effect = RateLimit()
        with self.assertRaises(ProviderError) as caught:
            await self.vision.aanalyze("probe", prompt="Read")
        self.assertEqual(caught.exception.code, "provider_rate_limit")
        self.assertEqual(self.complete.await_count, 1)
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def slow(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.complete.side_effect = slow
        pending = asyncio.create_task(self.vision.aanalyze("probe", prompt="Read"))
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.complete.await_count, 2)

    async def test_cancelled_preparation_does_not_publish(self):
        import threading
        started, release = threading.Event(), threading.Event()
        original = images.preprocess
        def slow(*args):
            started.set()
            release.wait(5)
            return original(*args)
        with patch.object(images, "preprocess", side_effect=slow):
            pending = asyncio.create_task(self.vision.apreprocess("probe", operations=[{"operation": "grayscale"}]))
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            pending.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await pending
            finally:
                release.set()
        self.assertEqual(list(await self.vision.alist()), ["probe"])

    async def test_empty_ocr_is_valid_but_not_a_rag_document(self):
        self.ocr.recognize = AsyncMock(return_value={"text": "", "blocks": []})
        self.assertEqual((await self.vision.aocr("probe", backend="first"))["text"], "")
        with self.assertRaisesRegex(ValueError, "no document text"):
            await self.vision.aextract_document("probe", mode="ocr", title="Empty", backend="first")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_approval_and_configuration_binding(self):
        async def ask(call):
            raise ToolApprovalRequired()
        for approve in (False, True):
            with self.subTest(approve=approve), tempfile.TemporaryDirectory() as root:
                ocr = OCR()
                async with LargeLanguageModel(root,
                        components=[VisionComponent(ocr_backends={"local": ocr}), WorkflowComponent()],
                        engines={"graph": GraphEngine(handlers={"tool": ToolNode()})},
                        services=ServiceConfig(tool_policy=ToolPolicy(authorize=ask))) as app:
                    project = await app.projects.acreate("Graph", components=["vision", "workflows"],
                        config=ProjectConfig(parameters={"components": {"vision": {"ocr": {"backend": "local"}}}}))
                    await project.components.vision.aimport_image(picture(), title="Probe", identifier="probe")
                    flow = (WorkflowGraph(entry="read")
                        .node("read", "tool", tool="image_ocr", arguments={"image_id": "probe"}, result_key="ocr")
                        .node("end", "end").connect("read", "end").to_dict())
                    await project.components.workflows.acreate(flow, identifier="read-image")
                    session = await project.sessions.acreate()
                    paused = await (await session.run.submit("Read", engine="graph", engine_options={"workflow": "read-image"})).wait(timeout=20)
                    self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
                    self.assertEqual(ocr.calls, [])
                    request, = await paused.ainteractions(pending_only=True)
                    await paused.arespond(request.respond("approve" if approve else "deny"))
                    await project.components.vision.aconfigure({"ocr": {"backend": "local", "timeout": 5}})
                    with self.assertRaisesRegex(Exception, "changed"):
                        await session.run.resume(paused.id, engine="graph")
                    await project.components.vision.aconfigure({"ocr": {"backend": "local"}})
                    resumed = await (await session.run.resume(paused.id, engine="graph")).wait(timeout=20)
                    self.assertEqual(resumed.data.status, RunStatus.COMPLETED if approve else RunStatus.FAILED, resumed.data.error)
                    self.assertEqual(len(ocr.calls), 1 if approve else 0)
                    if approve:
                        step = next(s for s in await resumed.steps.alist() if s.kind == "tool")
                        self.assertEqual(step.output.data["kind"], "ocr")

    async def test_loop_tool_step_and_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            complete = ScriptedCompletion(
                [chunk(calls=[call('{"image_id":"probe"}', name="image_ocr", call_id="ocr")]), chunk(finish="tool_calls")],
                [chunk("Check the server."), chunk(finish="stop")])
            async with LargeLanguageModel(root, components=[VisionComponent(ocr_backends={"local": OCR()})],
                    engines={"loop": LoopEngine(completion_fn=complete, max_iterations=2)}) as app:
                project = await app.projects.acreate("Images", components=["vision"], config=ProjectConfig(parameters={"engines": {"loop": {"completion": {"model": "test"}}}, "components": {"vision": {"ocr": {"backend": "local"}}}}))
                await project.components.vision.aimport_image(picture(), title="Probe", identifier="probe")
                session = await project.sessions.acreate()
                run = await (await session.run.submit("Read probe", engine="loop")).wait(timeout=20)
                self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
                step = next(s for s in await run.steps.alist() if s.kind == "tool")
                self.assertEqual(step.status, StepStatus.COMPLETED)
                self.assertEqual(step.output.data["backend"], "local")
                ids = (project.id, session.id, run.id)
            async with LargeLanguageModel(root, components=[VisionComponent(ocr_backends={"local": OCR()})]) as app:
                project = await app.projects.aload(ids[0])
                session = await project.sessions.aload(ids[1])
                self.assertEqual((await (await session.run.aload(ids[2])).aresult()).status, RunStatus.COMPLETED)
                self.assertEqual((await project.components.vision.aload("probe"))["width"], 80)

    async def test_rag_registers_same_extracted_document_and_source(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[VisionComponent(ocr_backends={"local": OCR()}),
                    RAGComponent(embedding=EmbeddingModel(model="test", embedding_fn=fake_embedding))]) as app:
                project = await app.projects.acreate("RAG", components=["vision", "rag"], config=ProjectConfig(parameters={"components": {"vision": {"ocr": {"backend": "local"}},
                    "rag": rag_settings({"extraction": {"failure_policy": "disabled"}})}}))
                image = await project.components.vision.aimport_image(picture(), title="Probe")
                doc = await project.components.vision.aextract_document(image["id"], mode="ocr", title="OCR source")
                await project.components.rag.aadd_document(**doc, identifier="manual")
                hits = await project.components.rag.asearch_documents("Connection", method="bm25")
                self.assertTrue(hits)
                stored = await project.components.rag.aget_document("manual")
                self.assertEqual(stored["metadata"]["vision"]["image"]["sha256"], image["sha256"])

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract CLI is not installed")
    async def test_real_tesseract_reads_text_and_boxes(self):
        with Image.new("RGB", (700, 110), "white") as image:
            ImageDraw.Draw(image).text((20, 20), "VISION TEST 123", fill="black", font=ImageFont.load_default(size=42))
            raw = BytesIO()
            image.save(raw, format="PNG")
        result = await TesseractBackend().recognize(raw.getvalue(), options={"language": "eng", "page_segmentation": 6})
        self.assertIn("VISION TEST 123", result["text"])
        self.assertTrue(result["blocks"])
        validate_ocr(result, 700, 110)

    async def test_tesseract_missing_and_subprocess_cancellation(self):
        with self.assertRaises(VisionError) as caught:
            await TesseractBackend(executable="/nonexistent/tesseract").recognize(picture(), options={})
        self.assertEqual(caught.exception.code, "vision_backend_unavailable")
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            pidfile, executable = root / "pid", root / "ocr"
            executable.write_text(f"#!{sys.executable}\nimport os, time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
            executable.chmod(0o700)
            task = asyncio.create_task(TesseractBackend(executable=str(executable)).recognize(picture(), options={}))
            async with asyncio.timeout(5):
                while not pidfile.exists():
                    await asyncio.sleep(0.01)
            pid = int(pidfile.read_text())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(Path(f"/proc/{pid}").exists())


if __name__ == "__main__":
    unittest.main()
