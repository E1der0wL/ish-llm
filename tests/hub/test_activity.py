"""Hub consumes the activity DTOs without operational-log or domain-file scans."""

import asyncio
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.ui.application import create_application
from hub.ui.chat.history_ui import format_activity
from hub.locales import Language
from hub.backend.runtime import HubConfig, HubRuntime
from llm.core.contracts import ProjectActivityEvent, ResourceRef
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput
from tests.hub.test_overlays import screen_text


def events():
    return (
        ProjectActivityEvent("2026-10-04T10:00:02+00:00", "run.started",
                             ResourceRef("run", "run-full-id", project_id="p", session_id="session-full-id",
                                         run_id="run-full-id"), "running"),
        ProjectActivityEvent("2026-10-04T10:00:01+00:00", "step.failed",
                             ResourceRef("step", "step-full-id", project_id="p", session_id="session-full-id",
                                         run_id="run-full-id", step_id="step-full-id"), "failed", "provider_unavailable"),
    )


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_activity_height_stays_large_until_terminal_shrinks(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            terminal = SizedOutput()
            app, controller = create_application(HubConfig(directory, auto_title=False), input=pipe, output=terminal)
            view = controller.view
            view.on_open = None
            rendered = asyncio.Event()
            app.after_render += lambda _: rendered.set()
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: app.is_running)
                with set_app(app):
                    controller.history_ui._activity_dialog("one short event")
                output = controller.history_ui.activity_output.window
                for rows, expected in ((48, 30), (40, 30), (24, 16), (16, 8), (40, 30)):
                    terminal.rows = rows
                    rendered.clear()
                    app._on_resize()
                    await asyncio.wait_for(rendered.wait(), 3)
                    position = app.renderer._last_screen.visible_windows_to_write_positions[output]
                    self.assertEqual(position.height, expected)
                    self.assertLessEqual(position.ypos + position.height, rows)
            finally:
                app.exit()
                await task
                controller.close()

    async def test_activity_scroll_without_output_focus_and_close_keys(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, "loop", "test/model", auto_title=False),
                                                input=pipe, output=SizedOutput())
            view = controller.view
            view.on_open = None
            view.project_id = "p"
            view.composer.text = "keep draft"
            rows = [events()[0].to_dict() for _ in range(80)]
            for row in rows:
                row["code"] = "long-code-" * 30
            def request(operation, *args, completed=None):
                completed(rows)
            with patch.object(controller, "_call", side_effect=request):
                task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
                try:
                    await until(lambda: app.is_running)
                    for close_key in ("\r", "\x1b", "\x0c"):
                        pipe.send_text("\x0c")
                        await until(lambda: view._dialog is not None and controller.history_ui.activity_output.window.render_info is not None)
                        output = controller.history_ui.activity_output
                        self.assertFalse(output.is_focusable())
                        pipe.send_text("\x1b[1;3H")
                        await until(lambda: output.top_line == 0)
                        pipe.send_text("\x1b[1;3B")
                        await until(lambda: output.top_line == 1)
                        pipe.send_text("\x1b[1;3C")
                        await until(lambda: output.left_column == 1)
                        pipe.send_text("\x1b[6;3~")
                        await until(lambda: output.top_line > 1)
                        pipe.send_text("ignored\t")
                        pipe.send_text(close_key)
                        await until(lambda: view._dialog is None)
                        self.assertIs(app.layout.current_control, view.composer.control)
                        self.assertEqual(view.composer.text, "keep draft")
                finally:
                    app.exit()
                    await task
                    controller.close()

    async def test_only_public_activity_api_and_commit_order(self):
        runtime = HubRuntime(HubConfig("unused", "loop", "test/model", auto_title=False))
        runtime.project = SimpleNamespace(aactivity=AsyncMock(return_value=events()))
        rows = await runtime.project_activity()
        runtime.project.aactivity.assert_awaited_once_with(limit=300, newest_first=False)
        self.assertEqual(rows, [event.to_dict() for event in events()])
        for language in ("ko", "en"):
            text = format_activity(rows, Language(language))
            self.assertLess(text.index("run.started"), text.index("step.failed"))
            for value in ("session-full-id", "run-full-id", "step-full-id", "provider_unavailable", "", ""):
                self.assertIn(value, text)
            self.assertNotIn("None", text)
        self.assertEqual(format_activity([], Language()), Language()("activity_empty"))

    async def test_popup_error_empty_stale_results_and_error_code(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, "loop", "test/model", auto_title=False),
                                                input=pipe, output=SizedOutput())
            view = controller.view
            view.on_open = None
            view.project_id = "p"
            callbacks = []
            def request(operation, *args, completed=None):
                callbacks.append((operation, completed))
            with patch.object(controller, "_call", side_effect=request):
                task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
                try:
                    await until(lambda: app.is_running)
                    for result in (None, [], [event.to_dict() for event in events()]):
                        previous = len(callbacks)
                        pipe.send_text("\x0c")
                        await until(lambda: len(callbacks) > previous)
                        self.assertEqual(callbacks[-1][0], "project_activity")
                        with set_app(app):
                            callbacks[-1][1](result)
                        if result is None:
                            self.assertIsNone(view._dialog)
                        else:
                            expected = "provider_unavailable" if result else view.t("activity_empty")
                            await until(lambda: expected in screen_text(app, 132, 40))
                            pipe.send_text("\x1b")
                            await until(lambda: view._dialog is None)
                    previous = len(callbacks)
                    pipe.send_text("\x0c")
                    await until(lambda: len(callbacks) > previous)
                    view.project_id = "different-project"
                    with set_app(app):
                        callbacks[-1][1]([event.to_dict() for event in events()])
                    self.assertIsNone(view._dialog)
                    previous = len(callbacks)
                    pipe.send_text("\x07")
                    await until(lambda: len(callbacks) > previous)
                    self.assertEqual(callbacks[-1][0], "turns")
                    with set_app(app):
                        callbacks[-1][1](None)
                    self.assertIsNone(view._dialog)
                finally:
                    app.exit()
                    await task
                    controller.close()
