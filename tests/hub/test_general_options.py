"""General categories affect preferences, startup, notifications and scrolling."""

import asyncio
from dataclasses import replace
from functools import partial
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.ui.application import create_application
from hub.widget.conversation import ChatMessage
from hub.config.general import GeneralSettings
from hub.ui.live import LiveHubView
from hub.locales import Language
from hub.widget.notifications import SessionToasts
from hub.config.preferences import PreferencesStore
from hub.backend.runtime import HubConfig, HubRuntime
from hub.model import HubSnapshot, SessionSummary
from hub.config.view_state import ViewStateStore
from hub.backend.worker import BackendWorker
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput
from tests.hub.test_settings import settings_backend


class GeneralOptionTests(unittest.TestCase):
    def test_language_packs_validate_and_fall_back(self):
        packs = {"custom": {"settings_title": "Custom settings", "elapsed": "Time {duration}"}}
        language = Language("custom", packs)
        self.assertEqual(language("settings_title"), "Custom settings")
        self.assertEqual(language("settings_profile"), Language("en")("settings_profile"))
        for invalid in ({"en": {"settings_title": "override"}}, {"bad": {"unknown_key": "value"}},
                        {"bad": {"settings_title": "{unknown}"}}, {"bad": {"elapsed": "{duration.__class__}"}}):
            with self.assertRaises(ValueError):
                GeneralSettings(language_packs=invalid)
        for values in ({"notification_seconds": 0}, {"notification_seconds": float("nan")},
                       {"auto_scroll": "false"}, {"notification_kinds": ["invalid"]}):
            with self.assertRaises(ValueError):
                GeneralSettings(**values)

    def test_response_following_can_be_disabled(self):
        view = LiveHubView()
        messages = (ChatMessage("assistant", "paragraph\n\n" * 100, id="first"),)
        snapshot = HubSnapshot("p", "Project", "model", "file", (SessionSummary("s", "Session", "running"),),
                               "s", messages, engines=("loop",))
        view.general = GeneralSettings(auto_scroll=False)
        view.apply_snapshot(snapshot)
        control = view.transcript.control
        control.create_content(80, 10)
        self.assertEqual(control.top_line, 0)
        control.scroll("end")
        old_line = control.top_line
        view.transcript.window.render_info = SimpleNamespace(last_visible_line=lambda: len(control._lines) - 1,
                                                             content_height=len(control._lines))
        snapshot = replace(snapshot, messages=(*messages, ChatMessage("assistant", "new\n\n" * 30, id="second")))
        view.apply_snapshot(snapshot)
        control.create_content(80, 10)
        self.assertEqual(control.top_line, old_line)
        view.general = GeneralSettings(auto_scroll=True)
        control.scroll("end")
        view.transcript.window.render_info = SimpleNamespace(last_visible_line=lambda: len(control._lines) - 1,
                                                             content_height=len(control._lines))
        view.apply_snapshot(replace(snapshot, messages=(*snapshot.messages, ChatMessage("assistant", "third", id="third"))))
        self.assertTrue(control.follow_tail)


class GeneralOptionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_alert_levels_duration_and_live_filter(self):
        view = SimpleNamespace(general=GeneralSettings(notification_kinds=["error"], notification_seconds=3), t=Language())
        toasts = SessionToasts(view)
        toasts.app = SimpleNamespace(invalidate=Mock())
        with patch("hub.widget.notifications.monotonic", return_value=100):
            toasts.push("Session", "completed", "success")
            toasts.push("Session", "failed", "error")
            self.assertEqual(len(toasts.items), 1)
            self.assertEqual(toasts.items[0].expires, 103)
            self.assertTrue(toasts.active())
        with patch("hub.widget.notifications.monotonic", return_value=104):
            self.assertFalse(toasts.active())
        toasts.alert("Error", "message", "error")
        view.general = GeneralSettings(notification_kinds=[])
        self.assertFalse(toasts.active())

    async def test_startup_restore_flags_explicit_selection_and_missing_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, "loop", "test/model", auto_title=False)
            runtime = HubRuntime(config, settings_backend)
            await runtime.start()
            if not runtime.sessions:
                await runtime.new_session()
            try:
                default_project, first_default = runtime.project.id, runtime.selected_id
                last_default = await runtime.new_session("new", "Default second")
                other = await runtime.backend.projects.acreate("Other", components=["tools"],
                    config={"parameters": {"engines": {"loop": {'config': {'completion': {'model': 'test/model'}}}}}}, conversation_storage="file")
                await runtime.activate_project(other)
                await runtime.new_session()
                first_other = runtime.selected_id
                last_other = await runtime.new_session("new", "Other second")
                other_id = other.id
            finally:
                await runtime.close()
            states = ViewStateStore(directory)
            states.save(default_project, first_default, {})
            states.save(other_id, first_other, {})
            prefs = PreferencesStore(directory)
            cases = [(True, True, config, other_id, first_other),
                     (True, False, config, other_id, last_other),
                     (False, True, config, default_project, first_default),
                     (False, False, config, default_project, last_default),
                     (True, True, replace(config, project_id=default_project, session_id=last_default), default_project, last_default)]
            for restore_project, restore_session, candidate, project, session in cases:
                prefs.save("general", {"restore_last_project": restore_project, "restore_last_session": restore_session})
                runtime = HubRuntime(candidate, settings_backend)
                try:
                    await runtime.start()
                    if not runtime.sessions:
                        await runtime.new_session()
                    self.assertEqual((runtime.project.id, runtime.selected_id), (project, session))
                finally:
                    await runtime.close()
            states.save("missing", "missing", {})
            runtime = HubRuntime(config, settings_backend)
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                self.assertEqual(runtime.project.id, default_project)
            finally:
                await runtime.close()

    async def test_categorized_form_pack_add_delete_save_and_reload(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, "loop", "test/model", auto_title=False)
            app, controller = create_application(config, input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_text("\x13")
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose("general")
                    manager = screen.page.general_form
                    manager.add_pack()
                self.assertEqual(tuple(manager.groups), ("language", "notifications", "startup", "output", "editor"))
                app.current_buffer.text = "custom"
                pipe.send_text("\t")
                await until(lambda: "settings_title" in app.current_buffer.text)
                app.current_buffer.text = json.dumps({"settings_title": "Custom Settings"})
                pipe.send_text("\t\r")
                await until(lambda: view._dialog is None)
                self.assertTrue(screen.page.dirty)
                manager.form.fields[("language",)].input.text = "custom"
                manager.form.fields[("notification_seconds",)].input.text = "3"
                manager.form.fields[("auto_scroll",)].input.text = "false"
                with set_app(app):
                    screen.page.submit()
                await until(lambda: not screen.busy and not screen.page.dirty)
                self.assertFalse(view.general.auto_scroll)
                self.assertEqual(view.general.notification_seconds, 3)
                self.assertEqual(view.t.code, "ko")
                next_app, next_controller = create_application(config, input=pipe, output=SizedOutput())
                self.assertEqual(next_controller.view.t("settings_title"), "Custom Settings")
                next_controller.close()
                with set_app(app):
                    screen.page.general_form.delete_pack()
                pipe.send_text("\t\r")
                await until(lambda: view._dialog is None)
                with set_app(app):
                    screen.page.submit()
                await until(lambda: not screen.busy and not screen.page.dirty)
                saved = PreferencesStore(directory).load()["general"]
                self.assertEqual(saved["language"], "en")
                self.assertEqual(saved["language_packs"], {})
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
