"""Hub consumers read the same current diagnostics as llm's public APIs."""

from llm.llm import Diagnostic

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from hub.errors.adapters import from_diagnostic
from llm.components.rag.jobs import RAGJobs
from llm.engines.loop import LoopEngine
from llm.services.runtime.tools import ToolExecutionError
from tests.hub.test_rag_jobs import settings
from tests.hub.test_tool_retry import ExampleTools
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class BackendDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_loop_checkpoint_query_and_partial_resume_via_public_backend(self):
        provider = ScriptedCompletion([chunk("partial reference"), ConnectionError("offline")],
                                      [chunk("done", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)}))
            await runtime.start()
            try:
                session = await runtime.project.sessions.acreate("resume")
                first = await (await session.run.submit("original request", engine="loop")).wait(timeout=10)
                self.assertEqual(str(first.data.status), "failed")
                self.assertEqual(await first.acheckpoint(), await first.acheckpoint("loop"))
                runtime.sessions[session.id] = session
                resumed_id = await runtime.retry_turn(session.id, first.data.input_message_id, first.id)
                await session.run.wait_idle()
                resumed, = [handle for handle in await session.run.alist()
                            if (await handle.aget_data()).input_message_id == resumed_id]
                self.assertEqual(str((await resumed.aresult()).status), "completed", resumed.data.error)
                self.assertIn("original request", str(provider.requests[-1]["messages"]))
                self.assertIn("partial reference", str(provider.requests[-1]["messages"]))
                self.assertEqual(len(provider.requests), 2)
            finally:
                await runtime.close()

    async def test_tool_result_and_step_give_hub_the_same_effect_facts(self):
        calls = []
        async def handler(arguments):
            calls.append(arguments)
            raise ToolExecutionError("unsupported input", effect="none", recoverable=True)
        provider = ScriptedCompletion([chunk(calls=[call('{"format":"old","count":1}', name="convert")],
                                              finish="tool_calls")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)},
                component_factories=(lambda: ExampleTools(handler),),
                project_config={"policies": {"tool_retry": {"max_retries": 0}}}))
            await runtime.start()
            try:
                session = await runtime.project.sessions.acreate("diagnostic")
                run = await (await session.run.submit("convert", engine="loop")).wait(timeout=10)
                result = await run.aresult()
                step, = [item for item in await run.steps.alist() if item.kind == "tool"]
                self.assertEqual(str(result.status), "failed")
                self.assertEqual(calls, [{"format": "old", "count": 1}])
                converted = [from_diagnostic(Diagnostic(issue.code or "execution_failed", issue.message, details=issue.details))
                             for issue in (result.diagnostic, step.diagnostic)]
                self.assertEqual(converted[0].details, converted[1].details)
                self.assertEqual(converted[0].details["effect"], "none")
                self.assertTrue(converted[0].details["recoverable"])
                self.assertEqual(len(provider.requests), 1)
            finally:
                await runtime.close()

    async def test_rag_retry_progress_success_and_reopen_have_no_current_failure(self):
        calls, updates = [], []
        async def embed(**request):
            calls.append(request["input"])
            return {"data": [{"embedding": [1., 2., 3.]}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = HubRuntime(settings(root, embed))
            await runtime.start()
            project_id = runtime.project.id
            try:
                rag = await runtime.project.components.aget("rag")
                job = await rag.aenqueue_document(identifier="note", title="Note", content="Sample")
                with patch.object(RAGJobs, "_commit", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        await runtime.manage_records(project_id, "rag", "retry", job["id"], progress=updates.append)
                original = await rag.ajob_diagnostic(job["id"])
                self.assertIsNotNone(updates[-1]["failure"])
                updates.clear()
                await runtime.manage_records(project_id, "rag", "retry", job["id"], progress=updates.append)
                self.assertEqual(updates[-1]["status"], "completed")
                self.assertIsNone(updates[-1]["failure"])
                self.assertEqual(updates[-1]["description"], "")
                self.assertIsNone(await rag.ajob_diagnostic(job["id"]))
                self.assertEqual(calls, [["Sample"]])
            finally:
                await runtime.close()
            reopened = HubRuntime(settings(root, embed))
            await reopened.start()
            try:
                project = await reopened.backend.projects.aload(project_id)
                rag = await project.components.aget("rag")
                stored = await rag.ajob(job["id"])
                self.assertEqual(stored["status"], "completed")
                self.assertIsNone(await rag.ajob_diagnostic(job["id"]))
                self.assertEqual(stored["failures"][0]["diagnostic"], original.to_dict())
                self.assertEqual((await rag.ajob_progress(job["id"])).message, "")
                self.assertEqual(calls, [["Sample"]])
            finally:
                await reopened.close()
