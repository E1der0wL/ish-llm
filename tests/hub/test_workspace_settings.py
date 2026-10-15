"""Changing the Hub data root takes effect only on the next startup."""

import asyncio
import os
from functools import partial
from pathlib import Path
import tempfile
import threading
import unittest
from tests.hub.support import open_home
from unittest.mock import patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.worker import BackendWorker
from hub.config.general import GeneralSettings
from hub.config.preferences import PreferencesStore, configured_profile, configured_workspace
from hub.config.workspace import workspace_path, validate_destination
from hub.ui.application import create_application
from tests.hub.support import settings_button, SizedOutput
from tests.hub.test_live import until
from tests.hub.test_settings import settings_backend


class WorkspaceSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_paths_and_destination_validation_do_not_create_directories(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "new folder" / "workspace"
            validate_destination(str(target))
            self.assertFalse(target.exists())
            with patch.dict(os.environ, {"HOME": root}):
                self.assertEqual(workspace_path("~/new folder"), Path(root) / "new folder")
            for value in (None, 42, "relative/path", "/tmp/bad\0path", "/tmp/a\nb"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    GeneralSettings(workspace_root=value)
            file = Path(root) / "file"
            file.write_text("keep")
            for value in (file, file / "child"):
                with self.assertRaises(ValueError):
                    validate_destination(str(value))
            with patch("os.access", return_value=False):
                with self.assertRaises(ValueError):
                    validate_destination(str(target))

    async def test_restart_switches_data_without_moving_old_sessions_or_chaining(self):
        with tempfile.TemporaryDirectory() as root:
            anchor, target, third = (Path(root) / name for name in ("initial", "selected", "third"))
            base = HubConfig(anchor, model="test/model", auto_title=False)
            other = HubRuntime(HubConfig(target, model="test/model", auto_title=False), settings_backend)
            await other.start()
            try:
                target_project = other.project.id
                target_session = await other.new_session(title="Target history")
            finally:
                await other.close()
            # The selected root's own launcher preferences must not redirect us.
            PreferencesStore(target).save("general", {"workspace_root": str(third)})
            gate = threading.Event()
            runtime = HubRuntime(base, partial(settings_backend, gate=gate))
            await runtime.start()
            try:
                original_project = runtime.project.id
                original_session = await runtime.new_session(title="Original history")
                request = await runtime.sessions[original_session].run.submit("hold original request", engine="loop")
                async with asyncio.timeout(5):
                    while not (await runtime.sessions[original_session].run.astatus()).active_run_id:
                        await asyncio.sleep(.01)
                await runtime.settings_service.save_global("profile", {"display_name": "Shared profile"})
                await runtime.settings_service.save_global("general", {"workspace_root": str(target), "editor": "nano"})
                self.assertEqual(runtime.config.workspace, anchor)
                self.assertEqual(runtime.project.id, original_project)
                self.assertEqual(configured_workspace(runtime.config).workspace, anchor)
                self.assertIsNotNone((await runtime.sessions[original_session].run.astatus()).active_run_id)
                gate.set()
                result = await request.wait()
                self.assertEqual(str((await result.aget_data()).status), "completed")
            finally:
                await runtime.close()
            configured = configured_profile(base)
            self.assertEqual(configured.workspace, target)
            self.assertEqual(configured.user_profile.display_name, "Shared profile")
            runtime = HubRuntime(configured, settings_backend)
            await runtime.start()
            try:
                self.assertEqual(runtime.project.id, target_project)
                self.assertIn(target_session, runtime.sessions)
                self.assertEqual(runtime.general.editor, "nano")
                self.assertEqual(runtime.settings_service.preferences.path, anchor / ".hub/preferences.json")
                await runtime.settings_service.save_global("general", {"workspace_root": str(third)})
                self.assertEqual(configured_workspace(base).workspace, third)
                self.assertFalse(third.exists())
                await runtime.settings_service.save_global("general", {"workspace_root": ""})
                self.assertEqual(configured_workspace(base).workspace, anchor)
            finally:
                await runtime.close()
            runtime = HubRuntime(base, settings_backend)
            await runtime.start()
            try:
                self.assertEqual(runtime.project.id, original_project)
                self.assertIn(original_session, runtime.sessions)
                conversation = await runtime.sessions[original_session].aconversation()
                self.assertEqual(conversation[-1].content, "**reply**: hold original request")
            finally:
                await runtime.close()

    async def test_invalid_destination_does_not_partially_save_global_batch(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False), settings_backend)
            await runtime.start()
            try:
                file = Path(root) / "not-a-directory"
                file.write_text("data")
                before = runtime.settings_service.preferences.load()
                with self.assertRaises(ValueError):
                    await runtime.settings_service.save_all([
                        ("save_global", ("profile", {"display_name": "Must not save"})),
                        ("save_global", ("general", {"workspace_root": str(file)}))])
                self.assertEqual(runtime.settings_service.preferences.load(), before)
            finally:
                await runtime.close()

    async def test_general_ui_cancel_and_save_keep_current_root_until_restart(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            anchor, target = Path(root) / "initial", Path(root) / "selected"
            config = HubConfig(anchor, model="test/model", auto_title=False)
            app, controller = create_application(config, input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_bytes(b'\x13')
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose("general")
                    screen.page.form.fields[("workspace_root",)].input.text = str(target)
                    screen.discard()
                await open_home(app, view)
                self.assertEqual(configured_workspace(config).workspace, anchor)
                pipe.send_bytes(b'\x13')
                await until(lambda: view.settings_open and not screen.busy)
                with set_app(app):
                    screen.choose("general")
                    field = screen.page.form.fields[("workspace_root",)]
                    app.layout.focus(field.input)
                    field.input.text = str(target)
                settings_button(app, pipe, view, "save")
                await until(lambda: view._dialog is not None and not screen.busy)
                pipe.send_text("\x1b")  # Keep the saved path for the next restart.
                await until(lambda: view._dialog is None)
                await open_home(app, view)
                self.assertEqual(configured_workspace(config).workspace, target)
                self.assertEqual(controller.config.workspace, anchor)
                self.assertEqual(controller.worker.runtime.config.workspace, anchor)
                self.assertFalse(target.exists())
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
