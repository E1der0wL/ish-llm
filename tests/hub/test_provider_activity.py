"""Real provider failures must be discoverable at RunManager's log destination."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from llm.engines.loop import LoopEngine

from hub.backend.observability.provider_logs import associate_provider_logs, read_provider_logs
from hub.backend.runtime import HubConfig, HubRuntime
from hub.locales import Language
from hub.ui.chat.history_ui import format_activity


class ProviderActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_run_failure_links_provider_traceback_to_activity(self):
        def provider(**request):
            error = RuntimeError("upstream response unavailable")
            error.status_code = 503
            raise error
            yield  # Exercise the stream_read boundary, before the first chunk.

        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                await runtime.submit(sid, "A failing request")
                await runtime.sessions[sid].run.wait_idle()
                rows = await runtime.project_activity()
                failure = next(row for row in rows if row["event"] == "run.failed")
                diagnostic = failure["provider_diagnostics"][0]
                self.assertEqual(diagnostic["code"], "provider_unavailable")
                self.assertIn("/projects/logs/", diagnostic["log_path"])
                details = diagnostic["details"]["details"]
                self.assertEqual(details["stage"], "stream_read")
                self.assertEqual(details["chunks_received"], 0)
                self.assertEqual(details["exceptions"][0]["status_code"], 503)
                self.assertTrue(details["exceptions"][0]["frames"])
                self.assertFalse(any(row.get("provider") for row in rows))
                for locale in ("ko", "en"):
                    rendered = format_activity(rows, Language(locale))
                    self.assertIn("HTTP 503", rendered)
                    self.assertIn("Traceback", rendered)
                    self.assertIn("provider()", rendered)
                    self.assertIn('"stage": "stream_read"', rendered)
                    self.assertNotIn("upstream response unavailable", rendered)
            finally:
                await runtime.close()

    def test_both_log_locations_filter_projects_and_preserve_source(self):
        with tempfile.TemporaryDirectory() as directory:
            for folder in ("logs", "projects/logs"):
                destination = Path(directory) / folder
                destination.mkdir(parents=True)
                records = []
                for owner in ("mine", "other"):
                    value = {"code": "provider_unavailable", "details": {"project_id": owner,
                             "session_id": "s", "run_id": "r", "stage": folder}}
                    records.append("2026-10-11 17:22:00,000 ERROR " + json.dumps(value))
                (destination / "providers-1.log").write_text("\n".join(records))
            rows = read_provider_logs(directory, "mine")
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["details"]["details"]["stage"] for row in rows}, {"logs", "projects/logs"})
            self.assertEqual(len({row["log_path"] for row in rows}), 2)

    def test_same_run_evidence_is_attached_once_without_reordering_or_mutation(self):
        source = {"session_id": "s", "run_id": "r"}
        activity = [{"event": "step.failed", "source": source}, {"event": "run.failed", "source": source}]
        provider = [{"provider": True, "source": source},
                    {"provider": True, "source": {"session_id": "other", "run_id": "r"}}]
        rows = associate_provider_logs(activity, provider)
        self.assertEqual([r.get("event") for r in rows], ["step.failed", "run.failed", None])
        self.assertNotIn("provider_diagnostics", rows[0])
        self.assertEqual(rows[1]["provider_diagnostics"], [provider[0]])
        self.assertTrue(all("provider_diagnostics" not in row for row in activity))
        fallback = associate_provider_logs(activity[:1], provider[:1])
        self.assertEqual(fallback[0]["provider_diagnostics"], [provider[0]])

    async def test_backend_workspace_is_authoritative_for_custom_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(Path(directory) / "ui-preferences", auto_title=False))
            runtime.backend = SimpleNamespace(workspace=Path(directory) / "backend")
            runtime.project = SimpleNamespace(id="mine", aactivity=AsyncMock(return_value=[]))
            log_dir = runtime.backend.workspace / "projects/logs"
            log_dir.mkdir(parents=True)
            value = {"code": "provider_unavailable", "details": {"project_id": "mine"}}
            (log_dir / "providers-1.log").write_text("2026-10-11 17:22:00,000 ERROR " + json.dumps(value))
            rows = await runtime.project_activity()
            self.assertEqual(rows[0]["code"], "provider_unavailable")
            self.assertEqual(Path(rows[0]["log_path"]).parent, log_dir)
