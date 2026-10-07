"""Terminal failures and independently configurable observation/render clocks."""

import asyncio
from dataclasses import asdict, replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.worker import BackendWorker
from hub.config.preferences import PreferencesStore
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import ChatMessage, HubSnapshot, SessionSummary
from hub.ui.live import LiveHubView
from hub.ui.presentation import present_message
from hub.widget.progress import ProgressControl
from llm.llm import LargeLanguageModel
from tests.hub.test_live import ControlledEngine


class FailedEngine(ControlledEngine):
    async def run(self, context):
        if context.messages[-1].content == "partial":
            yield "Partial response"
        raise RuntimeError("Provider unavailable")


class RenderIntervalTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_runs_replace_waiting_and_preserve_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, "loop", "test/model", auto_title=False),
                lambda config: LargeLanguageModel(config.workspace, components=[], engines={"loop": FailedEngine(None)}))
            view = LiveHubView()
            try:
                await runtime.start()
                for content in ("empty", "partial"):
                    identifier = await runtime.new_session()
                    await runtime.submit(identifier, content)
                    await runtime.sessions[identifier].run.wait_idle()
                    snapshot = await runtime.snapshot()
                    original = snapshot.messages[-1]
                    self.assertEqual(original.status, "failed")
                    view.apply_snapshot(snapshot, select_id=identifier)
                    displayed = view.transcript.control.messages[-1].text
                    self.assertIn(view.t("response_failed"), displayed)
                    self.assertNotIn(view.t("waiting"), displayed)
                    if content == "partial":
                        self.assertIn("Partial response", displayed)
                    view.set_theme(replace(view.theme, icon_style="unicode"))
                    self.assertEqual(view.transcript.control.messages[-1].text.count(view.t("response_failed")), 1)
                    self.assertNotIn(view.t("response_failed"), original.text)
            finally:
                view.output_renderers.close()
                view.progress.close()
                await runtime.close()

    def test_intervals_persist_validate_and_default_for_existing_preferences(self):
        self.assertEqual(HubTheme().output_refresh_interval, 0.1)
        self.assertEqual(HubTheme().progress_refresh_interval, 0.1)
        self.assertEqual(HubTheme.from_preferences({"foreground": "default"}).output_refresh_interval, 0.1)
        for key in ("output_refresh_interval", "progress_refresh_interval"):
            for invalid in (0, -1, 11, True, "0.1", float("inf"), float("nan")):
                with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                    HubTheme(**{key: invalid})
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(directory)
            theme = HubTheme(output_refresh_interval=0.25, progress_refresh_interval=0.4)
            store.save("appearance", asdict(theme))
            self.assertEqual(HubTheme(**store.load()["appearance"]), theme)
        t = Language("en")
        self.assertIn("response failed", present_message(ChatMessage("assistant", "", status="failed"), t).text)
        for status in ("interrupted", "cancelled", "paused", "completed"):
            self.assertNotIn(t("waiting"), present_message(ChatMessage("assistant", "", status=status), t).text)

    async def test_progress_frames_are_cached_between_ticks_and_retuned(self):
        view = LiveHubView()
        progress = view.progress
        loop, timer = Mock(), Mock()
        loop.call_later.return_value = timer
        try:
            view.set_theme(HubTheme(progress_refresh_interval=0.4))
            progress.start("Loading")
            control = ProgressControl(progress, lambda: "")
            with patch("hub.widget.progress.asyncio.get_running_loop", return_value=loop), \
                 patch.object(progress.progress, "make_tasks_table", wraps=progress.progress.make_tasks_table) as render:
                control.create_content(100, 1)
                control.create_content(100, 1)
                self.assertEqual(render.call_count, 1)
                self.assertEqual(loop.call_later.call_args.args[0], 0.4)
                loop.call_later.call_args.args[1]()  # Timer fires, invalidating this frame.
                control.create_content(100, 1)
                self.assertEqual(render.call_count, 2)
                view.set_theme(replace(view.theme, progress_refresh_interval=0.2))
                timer.cancel.assert_called()
                control.create_content(100, 1)
                self.assertEqual(loop.call_later.call_args.args[0], 0.2)
                self.assertEqual(render.call_count, 3)
        finally:
            progress.close()
            view.output_renderers.close()

    async def test_shorter_output_interval_wakes_pending_long_batch(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker.loop = asyncio.get_running_loop()
        worker._closed = False
        worker._output_interval = 10
        worker._interval_changed = asyncio.Event()
        worker._snapshot_task = None
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event())
        worker.runtime.dirty.set()
        published = asyncio.Event()
        async def snapshot():
            return "updated"
        worker.runtime.snapshot = snapshot
        worker.publish = lambda _: published.set()
        worker.report_error = Mock()
        task = asyncio.create_task(worker._refresh())
        try:
            await asyncio.sleep(0)
            self.assertFalse(worker.runtime.dirty.is_set())
            worker.set_output_interval(0.01)
            async with asyncio.timeout(2):
                await published.wait()
            self.assertEqual(worker._output_interval, 0.01)
            worker.report_error.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
