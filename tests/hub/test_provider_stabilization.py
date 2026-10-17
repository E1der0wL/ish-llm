"""Hub consumes provider diagnostics without taking over execution or retry policy."""

import asyncio
from dataclasses import asdict
from pathlib import Path
import tempfile
from time import monotonic
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from hub.backend.execution.retries import HubLoopEngine, RetryMonitor
from hub.backend.observability.failures import FailureDiagnostics
from hub.errors.adapters import from_diagnostic
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.general import GeneralSettings
from hub.config.preferences import PreferencesStore
from hub.config.restart import restart_changes, setting_metadata
from hub.locales import Language
from hub.model import ChatMessage, RetrySummary
from hub.ui.chat.history_ui import format_activity
from hub.ui.presentation import present_message
from hub.widget.forms.general_settings import GeneralForm
from llm.core.contracts import Diagnostic, ResourceRef
from llm.providers.runtime import diagnostic_scope
from tests.hub.test_response_reliability import HTTPFailure
from tests.llm.test_loop import ScriptedCompletion


class DiagnosticSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_retired_disable_value_is_not_loaded_or_presented(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            store._write_json({"version": 1, "general": {"provider_error_details": False}})
            values = store.load()["general"]
            self.assertNotIn("provider_error_details", values)
            form = GeneralForm(NS(t=Language()), values)
            self.assertNotIn(("provider_error_details",), form.form.fields)

    def test_settings_form_validates_and_requires_restart(self):
        defaults = asdict(GeneralSettings())
        self.assertNotIn("provider_error_details", defaults)
        self.assertTrue(defaults["provider_request_summary"])
        form = GeneralForm(NS(t=Language()), defaults)
        changed = dict(defaults, provider_request_summary=False,
                       provider_max_detail_chars=1000)
        for name in form.groups["diagnostics"]:
            field = form.form.fields[(name,)]
            field.input.text = str(changed[name]).lower()
            self.assertTrue(setting_metadata("general")[name]["requires_restart"])
        self.assertEqual(form.form.values(), changed)
        self.assertEqual(set(restart_changes("general", changed, defaults)),
                         {"general." + key for key in form.groups["diagnostics"]})
        for invalid in ({"provider_request_summary": 1},
                        {"provider_max_detail_chars": 0}, {"provider_max_detail_chars": True},
                        {"provider_max_detail_chars": 1.5}):
            with self.assertRaises(ValueError):
                GeneralSettings(**invalid)

    async def test_saved_settings_reach_provider_logs_and_steps_after_restart(self):
        def model(**request):
            error = HTTPFailure(400)
            error.body = {"error": {"message": "unsupported option", "param": "tools"}}
            raise error
            yield

        with tempfile.TemporaryDirectory() as root:
            anchor, workspace = Path(root) / "preferences", Path(root) / "workspace"
            values = dict(asdict(GeneralSettings()), workspace_root=str(workspace),
                          provider_request_summary=True)
            PreferencesStore(anchor).save("general", values)
            config = HubConfig(anchor, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: HubLoopEngine(completion_fn=model)},
                project_config={"parameters": {"engines": {"loop": {"policy": {"provider": {"max_attempts": 1}}}}}})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                self.assertEqual(runtime.config.workspace, workspace)
                options = runtime.backend.services.provider_diagnostics
                self.assertTrue(options.error_details)
                self.assertTrue(options.request_summary)
                self.assertEqual(options.max_detail_chars, 2000)
                sid = await runtime.new_session()
                handle = await (await runtime.sessions[sid].run.submit("private body", engine="loop")).wait()
                rows = await runtime.project_activity()
                rendered = format_activity(rows, Language())
                self.assertIn("unsupported option", rendered)
                self.assertIn("request_summary", rendered)
                self.assertNotIn("private body", rendered)
                with patch("hub.backend.observability.provider_logs.read_provider_logs", side_effect=AssertionError("No log lookup")):
                    diagnosis = (await runtime.diagnose_failures(runtime.project.id, sid, [handle.id]))[handle.id]
                self.assertEqual((diagnosis.reason, diagnosis.http_status), ("request", 400))
                await runtime.settings("save_global", "general", dict(values, provider_request_summary=False))
                self.assertIs(runtime.backend.services.provider_diagnostics, options)
            finally:
                await runtime.close()
            restarted = HubRuntime(config)
            try:
                await restarted.start()
                self.assertTrue(restarted.backend.services.provider_diagnostics.error_details)
                self.assertFalse(restarted.backend.services.provider_diagnostics.request_summary)
            finally:
                await restarted.close()


class ActualRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_retry_after_and_jitter_delays_reach_countdown(self):
        for options, expected in (({"respect_retry_after": True}, 30), ({"jitter": True}, 2)):
            with self.subTest(options=options):
                error = HTTPFailure(429)
                error.response = NS(headers={"Retry-After": "30"})
                monitor = RetryMonitor()
                monitor.set(("run", "recovery"), "run", RetrySummary(1, 3, kind="recovery", phase="retrying"))
                engine = HubLoopEngine(completion_fn=ScriptedCompletion([error]))
                engine.retry_monitor = monitor
                ready, captured = asyncio.Event(), []
                def observe(event):
                    if event.code == "provider_retry":
                        captured.append((event, monitor.view("run"), monotonic()))
                        ready.set()
                async def consume():
                    token = monitor.run.set("run")
                    try:
                        with diagnostic_scope(observe):
                            async for _ in engine.stream_completion({"model": "test/model"}, provider={
                                "max_attempts": 3, "delay_seconds": 10, "max_delay_seconds": 60, **options}):
                                pass
                    finally:
                        monitor.run.reset(token)
                with patch("llm.policies.retry.random.uniform", return_value=2):
                    task = asyncio.create_task(consume())
                    try:
                        await asyncio.wait_for(ready.wait(), 5)
                        event, state, observed_at = captured[0]
                        self.assertEqual(event.details["delay_seconds"], expected)
                        self.assertAlmostEqual(state.retry_at - observed_at, expected, delta=.1)
                        self.assertEqual((state.attempt, state.maximum), (1, 3))
                        self.assertIsNone(monitor.call.get())
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(monitor.states, {})

    async def test_retry_after_above_cap_does_not_invent_a_countdown(self):
        error = HTTPFailure(503)
        error.response = NS(headers={"Retry-After": "30"})
        provider = ScriptedCompletion([error])
        monitor = RetryMonitor()
        engine = HubLoopEngine(completion_fn=provider)
        engine.retry_monitor = monitor
        observed = []
        token = monitor.run.set("run")
        try:
            with diagnostic_scope(observed.append), self.assertRaises(Exception):
                async for _ in engine.stream_completion({"model": "test/model"}, provider={
                    "max_attempts": 3, "max_delay_seconds": 10, "respect_retry_after": True}):
                    self.assertIsNone(monitor.view("run"))
        finally:
            monitor.run.reset(token)
        self.assertIn("provider_retry_stopped", [event.code for event in observed])
        self.assertNotIn("provider_retry", [event.code for event in observed])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(monitor.states, {})


class StoredDiagnosisTests(unittest.IsolatedAsyncioTestCase):
    def reader(self, steps, code):
        run = NS(status="failed", error_code=code, error="failed")
        handle = NS(aget_data=AsyncMock(return_value=run), steps=NS(alist=AsyncMock(return_value=steps)),
            aresult=AsyncMock(return_value=NS(diagnostic=Diagnostic(code or "execution_failed", "failed"))))
        project = NS(sessions=NS(aload=AsyncMock(return_value=NS(run=NS(aload=AsyncMock(return_value=handle))))),
                     aactivity=AsyncMock(side_effect=AssertionError("No activity lookup")))
        runtime = NS(backend=NS(projects=NS(aload=AsyncMock(return_value=project)), workspace="unused"),
                     config=NS(workspace="unused"))
        return FailureDiagnostics(runtime)

    def step(self, code, *, details=None, kind="llm", stamp="2026-10-16T01:00:00+00:00", status="failed"):
        return NS(id="step", kind=kind, started_at=stamp, created_at=stamp, ended_at=stamp, status=status,
                  diagnostic=Diagnostic(code, "failed", source=ResourceRef("step", "step", step_id="step",
                      project_id="p", session_id="s", run_id="r"), details=details or {}))

    def test_new_codes_have_specific_localized_guidance_and_preserve_output(self):
        cases = [("completion_incomplete", "length", "completion_length"),
                 ("completion_incomplete", "content_filter", "completion_filtered"),
                 ("completion_incomplete", "", "completion_incomplete"),
                 ("completion_invalid_response", "", "completion_invalid"),
                 ("completion_output_limit", "", "completion_output_limit"),
                 ("completion_tool_limit", "", "completion_tool_limit"),
                 ("completion_argument_limit", "", "completion_argument_limit"),
                 ("step_timeout", "", "step_timeout")]
        for code, finish, kind in cases:
            diagnosis = from_diagnostic(Diagnostic(code or "execution_failed", "", details={"finish_reason": finish}))
            self.assertEqual(diagnosis.reason, Diagnostic(code, "", details={"finish_reason": finish}).classification.reason)
            for language in ("ko", "en"):
                t = Language(language)
                rendered = present_message(ChatMessage("assistant", "partial", status="failed", failure=diagnosis), t)
                self.assertEqual(rendered.role, "system")
                self.assertNotIn("partial", rendered.text)
                self.assertIn(t("failure_" + kind), rendered.text)
                if finish:
                    self.assertIn("finish_reason=" + finish, rendered.text)

    async def test_step_diagnostic_wins_without_reading_logs(self):
        reader = self.reader([self.step("completion_incomplete", details={"finish_reason": "length"})],
                             "completion_incomplete")
        with patch("hub.backend.observability.provider_logs.read_provider_logs", side_effect=AssertionError("No log lookup")):
            result = await reader.read("p", "s", ["r"])
            self.assertEqual(result["r"].reason, "output")
            self.assertEqual(await reader.read("p", "s", ["r"]), result)

    async def test_recovered_step_and_other_run_diagnostic_are_not_used(self):
        recovered = self.step("provider_unavailable", details={"exceptions": [{"status_code": 503}]})
        last = self.step("step_failed", kind="tool", stamp="2026-10-16T02:00:00+00:00")
        wrong_owner = self.step("provider_unavailable", details={"run_id": "other"})
        for steps, code in (([recovered, last], "engine_failed"), ([wrong_owner], "provider_unavailable")):
            with patch("hub.backend.observability.provider_logs.read_provider_logs", side_effect=AssertionError("No log lookup")):
                result = (await self.reader(steps, code).read("p", "s", ["r"]))["r"]
                self.assertIsNone(result.http_status)
                if code == "engine_failed":
                    self.assertEqual(result.reason, "unknown")

    async def test_failed_graph_parent_is_not_hidden_by_a_later_cancelled_sibling(self):
        parent = self.step("completion_incomplete", kind="graph_node", details={"finish_reason": "length"})
        sibling = self.step("step_failed", stamp="2026-10-16T02:00:00+00:00", status="cancelled")
        with patch("hub.backend.observability.provider_logs.read_provider_logs", side_effect=AssertionError("No log lookup")):
            result = (await self.reader([parent, sibling], "completion_incomplete").read("p", "s", ["r"]))["r"]
        self.assertEqual(result.reason, "output")

    async def test_step_diagnosis_does_not_require_run_code_or_activity(self):
        step = self.step("provider_invalid_request", details={"exceptions": [{"status_code": 400}]})
        with patch("hub.backend.observability.provider_logs.read_provider_logs", side_effect=AssertionError("No log lookup")):
            result = (await self.reader([step], None).read("p", "s", ["r"]))["r"]
        self.assertEqual((result.code, result.reason, result.http_status), ("provider_invalid_request", "request", 400))
