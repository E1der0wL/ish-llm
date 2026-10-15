"""Question alerts, editable-field navigation and provider log projection."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input, DummyInput

from examples.hub.preview import create_preview
from hub.backend.observability.provider_logs import read_provider_logs
from hub.backend.runtime import HubConfig, HubRuntime
from hub.locales import Language
from hub.ui.chat.history_ui import format_activity
from tests.hub.support import SizedOutput, eventually


class StatusLogsNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_event_from_another_project_is_not_lost_or_repeated(self):
        runtime = HubRuntime(HubConfig("unused", auto_title=False))
        runtime.notifications.register_session("other-project", "s", "Other project session")
        run = NS(id="r", session_id="s", engine="loop", status="paused")
        await runtime._run_event(NS(run=run))
        self.assertEqual(runtime.notifications.items()[-1].status, "paused")
        runtime.notifications.record_question("other-project", "s", "Other project session",
                                       {"request": {"id": "approval", "kind": "approval"}})
        await runtime._run_event(NS(run=run))
        self.assertEqual([n.status for n in runtime.notifications.items()], ["paused", "waiting_approval"])

    async def test_question_notifications_reach_other_session_and_minimized_ui(self):
        from hub.model import HubSnapshot, SessionSummary, SessionNotification
        from hub.ui.application import create_application
        from tests.hub.test_overlays import screen_text
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(root, auto_title=False), input=pipe, output=SizedOutput())
            view = controller.view
            view.on_open = None
            controller._state_project = "p"
            base = HubSnapshot("p", "Project", "model", "file",
                (SessionSummary("s1", "First", "idle"), SessionSummary("s2", "Second", "idle")), "s1", ())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    controller.bind_app(app)
                    controller._snapshot(base)
                    notification = SessionNotification(1, "p", "s2", "Second", "waiting_input")
                    waiting = replace(base, sessions=(base.sessions[0], replace(base.sessions[1], status="waiting_input")),
                                      notifications=(notification,))
                    controller._snapshot(waiting)
                await eventually(lambda: "답변 대기" in screen_text(app, 132, 40))
                self.assertEqual(view.sessions[view.selected].id, "s1")
                self.assertEqual(view.toasts.items[-1].title, "Second")
                self.assertIs(app.layout.current_control, view.composer.control)
                with set_app(app):
                    controller._snapshot(base)
                    controller._snapshot(replace(base, notifications=(replace(notification, sequence=2),)))
                    self.assertFalse(view.toasts.items)
                    view.hide(app)
                    previous = app.layout.current_control
                    controller._snapshot(replace(base,
                        sessions=(replace(base.sessions[0], status="waiting_approval"), base.sessions[1]),
                        notifications=(SessionNotification(3, "p", "s1", "First", "waiting_approval"),)))
                await eventually(lambda: "승인 대기" in screen_text(app, 132, 40))
                self.assertFalse(view.visible)
                self.assertIs(app.layout.current_control, previous)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_settings_left_returns_to_panel_only_when_not_editing(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text('\x10\r')
                await eventually(lambda: view.settings_open and view.settings.page is not None)
                with set_app(app):
                    view.settings.choose("profile")
                    view.settings.layout.focus_main()
                field = view.settings.page.form.fields[("display_name",)]
                await eventually(lambda: app.layout.current_control is field.input.control)
                pipe.send_text("\x1b[D")
                await eventually(lambda: app.layout.current_control is view.settings.global_list)
                pipe.send_text("\x1b[C\r")
                await eventually(lambda: field.editing)
                field.input.text = "abc"
                field.input.buffer.cursor_position = 3
                pipe.send_text("\x1b[D")
                await eventually(lambda: field.input.buffer.cursor_position == 2)
                self.assertIs(app.layout.current_control, field.input.control)
                pipe.send_text("\r\x1b[D")
                await eventually(lambda: app.layout.current_control is view.settings.global_list)
                self.assertEqual(field.input.text, "abc")
            finally:
                app.exit()
                await task
                view.close()
                view.progress.close()

    async def test_real_provider_diagnostic_appears_with_project_activity(self):
        from llm.providers.runtime import diagnostic, logging_scope
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, auto_title=False))
            try:
                await runtime.start()
                with logging_scope(directory, project_id=runtime.project.id, session_id="session", run_id="run"):
                    diagnostic("provider_unavailable", severity="error", operation="completion", stage="stream")
                rows = await runtime.project_activity()
                row = next(row for row in rows if row.get("code") == "provider_unavailable")
                self.assertEqual(row["source"]["run_id"], "run")
                self.assertEqual(row["scope"], "project")
                text = format_activity(rows, Language())
                self.assertIn("프로바이더 로그", text)
                self.assertIn("provider_unavailable", text)
                self.assertIn('"stage": "stream"', text)
            finally:
                await runtime.close()

    def test_provider_tail_filters_other_projects_and_handles_partial_records(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory) / "logs"
            logs.mkdir()
            lines = ["partial record", "2026-10-11 10:00:00,000 WARNING {invalid"]
            for index, project in enumerate(("other", "mine", None, "mine")):
                value = {"code": f"event_{index}", "details": {"project_id": project, "session_id": "s", "run_id": "r"}}
                lines.append(f"2026-10-11 10:00:0{index},000 ERROR " + json.dumps(value))
            (logs / "providers-123.log").write_text("\n".join(lines), encoding="utf-8")
            rows = read_provider_logs(directory, "mine")
            self.assertEqual([row["code"] for row in rows], ["event_1", "event_2", "event_3"])
            self.assertEqual(rows[1]["scope"], "workspace")
            self.assertEqual(len(read_provider_logs(directory, "mine", limit=2)), 2)
            self.assertIn("워크스페이스 공통", format_activity(rows, Language()))

    async def test_question_toasts_use_warning_and_clear_on_resume(self):
        from hub.model import SessionSummary
        _, view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        try:
            for status in ("waiting_input", "waiting_approval"):
                view.toasts.push("Session title", status, ("p", "s"))
                self.assertEqual(view.toasts.items[-1].level, "warning")
                self.assertEqual(view.toasts.items[-1].title, "Session title")
                view.toasts.reconcile_waiting("p", (SessionSummary("s", "Session title", status),))
                self.assertTrue(view.toasts.items)
                view.toasts.reconcile_waiting("p", (SessionSummary("s", "Session title", "running"),))
                self.assertFalse(view.toasts.items)
        finally:
            view.close()
            view.progress.close()
