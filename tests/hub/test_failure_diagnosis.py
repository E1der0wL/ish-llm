"""Run-scoped explanations do not invoke a model or retry side effects."""

from llm.llm import Diagnostic

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hub.backend.observability.failures import FailureDiagnostics
from hub.errors.adapters import from_diagnostic
from hub.model import ChatMessage
from hub.locales import Language
from hub.ui.presentation import present_message


class DiagnosisTests(unittest.IsolatedAsyncioTestCase):
    def test_old_failures_do_not_churn_the_bounded_diagnostic_cache(self):
        from hub.ui.live import LiveController
        messages = [ChatMessage("assistant", "", status="failed", run_id=str(i)) for i in range(300)]
        controller = SimpleNamespace(view=SimpleNamespace(failure_diagnoses={
            ("p", "s", item.run_id): object() for item in messages[-256:]}),
            _diagnosing=set(), requests=Mock())
        LiveController._diagnose(controller, SimpleNamespace(project_id="p", selected_id="s", messages=messages))
        controller.requests.call.assert_not_called()

    async def test_failure_is_explained_in_live_output_without_another_model_call(self):
        import asyncio
        from prompt_toolkit.input import create_pipe_input
        from hub.ui.application import create_application
        from hub.backend.runtime import HubConfig
        from llm.engines.loop import LoopEngine
        from tests.hub.support import SizedOutput
        from tests.hub.test_live import until
        calls = []
        def provider(**request):
            calls.append(request)
            error = RuntimeError("test unavailable")
            error.status_code = 503
            raise error
            yield
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            config = HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)},
                project_config={"parameters": {"engines": {"loop": {"policy": {"provider": {"max_attempts": 1}}}}}})
            app, controller = create_application(config, input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_text("/new\r\r")
                await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("Fail this request\r")
                try:
                    await until(lambda: any("HTTP 503" in item.text for item in view.output.transcript.control.messages))
                except TimeoutError:
                    self.fail(f"diagnoses={view.failure_diagnoses!r}; notice={view.notice}; calls={len(calls)}; "
                              f"messages={view.output.transcript.control.messages!r}")
                self.assertEqual(len(calls), 1)
                answer = next(item for item in view.output.transcript.control.messages if item.role == "system")
                self.assertIn(view.t("failure_unavailable"), answer.text)
                self.assertIn("test unavailable", answer.text)
                self.assertTrue(answer.run_id)
            finally:
                app.exit()
                await task
                await controller.aclose()

    def test_structured_causes_and_localized_explanations_preserve_partial_answer(self):
        for status, kind in ((401, "authentication"), (403, "authentication"), (429, "rate_limit"),
                             (503, "unavailable"), (400, "request")):
            diagnosis = from_diagnostic(Diagnostic({401:"provider_authentication",403:"provider_authentication",429:"provider_rate_limit",503:"provider_unavailable",400:"provider_invalid_request"}[status], "", details={"exceptions": [
                {"type": "APIError", "status_code": status}, {"type": "RuntimeError"}]}))
            self.assertEqual((diagnosis.reason, diagnosis.http_status), (kind, status))
            message = ChatMessage("assistant", "Partial answer", status="failed", failure=diagnosis)
            for language in ("ko", "en"):
                t = Language(language)
                rendered = present_message(message, t).text
                self.assertNotIn("Partial answer", rendered)
                self.assertIn(t("failure_" + kind), rendered)
                self.assertIn(f"HTTP {status}", rendered)
        self.assertEqual(from_diagnostic(Diagnostic("engine_failed" or "execution_failed", "")).reason, "unknown")
        self.assertEqual(from_diagnostic(Diagnostic("provider_timeout" or "execution_failed", "")).reason, "timeout")

    async def test_logs_are_not_used_when_step_diagnostics_are_absent(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "projects/logs"
            directory.mkdir(parents=True)
            records = []
            for owner, sid, rid, status in (("p", "s", "r", 503), ("p", "other", "r", 401),
                                           (None, "s", "r", 429), ("p", "s", "other", 401)):
                value = {"code": "provider_unavailable", "details": {"project_id": owner, "session_id": sid,
                    "run_id": rid, "exceptions": [{"type": "APIError", "status_code": status}]}}
                records.append("2026-10-15 10:00:00,000 ERROR " + json.dumps(value))
            (directory / "providers-test.log").write_text("\n".join(records))
            run = SimpleNamespace(status="failed", error="provider unavailable", error_code="provider_unavailable")
            handle = SimpleNamespace(aget_data=AsyncMock(return_value=run), steps=SimpleNamespace(alist=AsyncMock(return_value=[])),
                aresult=AsyncMock(side_effect=lambda: SimpleNamespace(diagnostic=Diagnostic(run.error_code or "execution_failed", run.error))))
            session = SimpleNamespace(run=SimpleNamespace(aload=AsyncMock(return_value=handle)))
            project = SimpleNamespace(sessions=SimpleNamespace(aload=AsyncMock(return_value=session)),
                                      aactivity=AsyncMock(side_effect=AssertionError("No activity lookup")))
            runtime = SimpleNamespace(backend=SimpleNamespace(workspace=root,
                projects=SimpleNamespace(aload=AsyncMock(return_value=project))), config=SimpleNamespace(workspace=root))
            reader = FailureDiagnostics(runtime)
            result = await reader.read("p", "s", ["r"])
            self.assertEqual(result["r"].reason, "unavailable")
            self.assertIsNone(result["r"].http_status)
            with patch("hub.backend.observability.provider_logs.read_provider_logs", side_effect=AssertionError("No log lookup")):
                self.assertEqual(await reader.read("p", "s", ["r"]), result)
                for code, kind in (("engine_failed", "unknown"), (None, "unknown"), ("run_timeout", "timeout")):
                    reader.cache.clear()
                    run.error_code = code
                    self.assertEqual((await reader.read("p", "s", ["r"]))["r"].reason, kind)
            project.aactivity.assert_not_awaited()
