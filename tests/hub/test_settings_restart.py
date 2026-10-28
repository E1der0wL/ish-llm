"""Settings metadata, saved-value prompts, and actual shell Hub reinstallation."""

import asyncio
from dataclasses import asdict
from functools import partial
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig
from hub.backend.worker import BackendWorker
from hub.config.general import GeneralSettings
from hub.config.preferences import PreferencesStore, configured_profile
from hub.config.restart import restart_changes, restart_config, setting_metadata
from hub.hub import install
from hub.ui.application import create_application
from hub.ui.restart import RESTART
from hub.widget.dialog import PopupSize
from tests.hub.support import SizedOutput, render_container
from tests.hub.test_ish_integration import Prompt
from tests.hub.test_live import until
from tests.hub.test_settings import settings_backend


class RestartMetadataTests(unittest.TestCase):
    def test_metadata_is_not_persisted_and_paths_compare_semantically(self):
        meta = setting_metadata("general")
        self.assertTrue(meta["workspace_root"]["requires_restart"])
        self.assertFalse(meta["editor"]["requires_restart"])
        self.assertNotIn("requires_restart", asdict(GeneralSettings()))
        before = asdict(GeneralSettings())
        after = {**before, "workspace_root": "/tmp/hub", "editor": "nano"}
        self.assertEqual(restart_changes("general", after, before, anchor=Path("/tmp/hub")), [])
        after["language"] = "en"
        self.assertEqual(restart_changes("general", after, before, anchor=Path("/tmp/hub")), ["general.language"])

    def test_new_root_drops_old_explicit_selection(self):
        with tempfile.TemporaryDirectory() as root:
            launch = HubConfig(Path(root) / "original", project_id="old-project", session_id="old-session")
            current = configured_profile(launch)
            PreferencesStore(launch.workspace).save("general", {"workspace_root": str(Path(root) / "target")})
            config = restart_config(launch, current)
            self.assertIsNone(config.project_id)
            self.assertIsNone(config.session_id)
            self.assertEqual(config.workspace, Path(root) / "target")


class RestartUITests(unittest.IsolatedAsyncioTestCase):
    async def test_shortcut_and_header_confirm_before_cleanup(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(root, auto_title=False), input=pipe,
                output=SizedOutput(), worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected and not view.projects.busy)
                view.composer.text = 'keep draft'
                with patch.object(controller.worker, 'prepare_restart', wraps=controller.worker.prepare_restart) as prepare:
                    for source in ('shortcut', 'button'):
                        if source == 'shortcut':
                            pipe.send_bytes(b'\x17')
                        else:
                            # Exercise the shared button action; mouse dispatch
                            # itself is covered by test_mouse_widgets.
                            with set_app(app):
                                view.header.restart._activate()
                        await until(lambda: view._dialog is not None)
                        self.assertEqual(view._dialog.size, PopupSize.SMALL)
                        with set_app(app):
                            rendered = '\n'.join(render_container(view._dialog, 60, 9))
                        self.assertIn('Hub를 재시작할까요?', rendered)
                        self.assertIn('예약 요청을 중단합니다.', rendered)
                        self.assertIn('취소', rendered)
                        self.assertNotIn('Window too small', rendered)
                        prepare.assert_not_called()
                        self.assertFalse(controller.worker._restarting)
                        pipe.send_bytes(b'\x1b')
                        await until(lambda: view._dialog is None)
                        self.assertEqual(view.composer.text, 'keep draft')
                        self.assertFalse(task.done())
                    pipe.send_bytes(b'\x17')
                    await until(lambda: view._dialog is not None)
                    pipe.send_bytes(b'\r')
                    self.assertEqual(await asyncio.wait_for(asyncio.shield(task), 5), RESTART)
                    prepare.assert_called_once()
            finally:
                if not task.done():
                    app.exit()
                await task
                await controller.aclose()

    async def test_empty_workspace_restart_initializes_real_backend(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            original = Path(root) / 'original'
            target = Path(root) / 'new parent' / 'empty workspace'
            prompt = Prompt(input=pipe, output=SizedOutput())
            old = install(prompt, config=HubConfig(original, auto_title=False))
            app = prompt.app
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: old.view.show(app)))
            try:
                await until(lambda: old.view.connected and not old.view.projects.busy)
                old_project = old.view.project_id
                pipe.send_text('\x10\r')
                screen = old.view.settings
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose('general')
                    screen.page.form.fields[('workspace_root',)].input.text = str(target)
                    screen.save()
                await until(lambda: old.view._dialog is not None and not screen.busy)
                self.assertEqual(old.view._dialog.size, PopupSize.SMALL)
                self.assertFalse(target.exists())
                pipe.send_bytes(b'\r')
                await until(lambda: prompt._hub_ui_preview is not old)
                replacement = prompt._hub_ui_preview
                await until(lambda: replacement.view.connected and not replacement.view.projects.busy)
                self.assertIsNone(replacement.view._dialog)
                self.assertTrue(target.is_dir())
                self.assertEqual(replacement.controller.config.workspace, target)
                self.assertNotEqual(replacement.view.project_id, old_project)
                self.assertTrue((original / 'projects' / old_project / 'project.json').is_file())
                self.assertTrue((target / 'projects' / replacement.view.project_id / 'project.json').is_file())
                self.assertEqual(replacement.controller.config._workspace_anchor, original)
                self.assertFalse(old.controller.worker._thread.is_alive())
            finally:
                if not task.done():
                    app.exit(result='')
                await task
                await prompt._hub_ui_preview.controller.aclose()
                prompt._hub_ui_preview.close()

    async def test_save_then_escape_retains_saved_values_and_enter_requests_restart(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            config = HubConfig(root, model="test/model", auto_title=False)
            app, controller = create_application(config, input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected and not view.projects.busy)
                pipe.send_text('\x10\r')
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose("general")
                    screen.page.form.fields[("language",)].input.text = "en"
                    screen.page.save.handler()
                await until(lambda: view._dialog is not None and not screen.busy)
                self.assertEqual(view._dialog.size, PopupSize.SMALL)
                with set_app(app):
                    self.assertEqual(view._dialog._width().preferred, 60)
                    self.assertEqual(view._dialog._height().preferred, 9)
                self.assertEqual(PreferencesStore(root).load()["general"]["language"], "en")
                pipe.send_bytes(b'\x1b')
                await until(lambda: view._dialog is None)
                self.assertFalse(task.done())
                self.assertEqual(controller.config.language, "ko")
                # A save without another edit still offers the pending restart.
                with set_app(app):
                    screen.save()
                await until(lambda: view._dialog is not None)
                pipe.send_bytes(b'\r')
                self.assertEqual(await asyncio.wait_for(asyncio.shield(task), 5), RESTART)
            finally:
                if not task.done():
                    app.exit()
                    await task
                await controller.aclose()

    async def test_live_setting_save_and_failed_root_save_do_not_prompt(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(root, auto_title=False), input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected and not view.projects.busy)
                pipe.send_text('\x10\r')
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose("general")
                    screen.page.form.fields[("editor",)].input.text = "nano"
                    screen.page.save.handler()
                await until(lambda: not screen.busy)
                self.assertIsNone(view._dialog)
                invalid = Path(root) / "file"
                invalid.write_text("keep")
                with set_app(app):
                    screen.page.form.fields[("workspace_root",)].input.text = str(invalid)
                    screen.page.save.handler()
                await until(lambda: not screen.busy)
                self.assertIsNone(view._dialog)
                self.assertTrue(screen.page.dirty)
            finally:
                app.exit()
                await task
                await controller.aclose()

    async def test_restart_interrupts_active_run_and_cancels_queue_without_execution(self):
        with tempfile.TemporaryDirectory() as root:
            gate = threading.Event()
            worker = BackendWorker(HubConfig(root, auto_title=False), lambda value: None, lambda error: None,
                backend_factory=partial(settings_backend, gate=gate))
            try:
                session = await asyncio.wrap_future(worker.call("new_session", "new", "Running"))
                await asyncio.wrap_future(worker.call("submit", session, "hold request", "assistant"))
                async def active():
                    return (await worker.runtime.sessions[session].run.astatus()).active_runs
                await until(lambda: bool(worker.runtime.sessions))
                async with asyncio.timeout(10):
                    while not await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(active(), worker.loop)):
                        await asyncio.sleep(.01)
                await asyncio.wrap_future(worker.call("submit", session, "queued request", "assistant"))
                await asyncio.wait_for(asyncio.wrap_future(worker.prepare_restart()), 15)
                self.assertTrue(worker._restarting)
                async def verify():
                    handle = worker.runtime.sessions[session]
                    state = await handle.run.astatus()
                    self.assertEqual(state.active_runs, [])
                    self.assertEqual(state.queued_count, 0)
                    runs = await handle.run.alist()
                    self.assertEqual(len(runs), 1)
                    self.assertEqual(str((await runs[0].aget_data()).status), "interrupted")
                    messages = await handle.aconversation()
                    self.assertTrue(any(m.content == "queued request" and str(m.status) == "cancelled" for m in messages))
                await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(verify(), worker.loop))
                gate.set()
            finally:
                gate.set()
                await asyncio.to_thread(worker.close)

    async def test_idle_restart_freezes_admission_until_cancelled(self):
        with tempfile.TemporaryDirectory() as root:
            worker = BackendWorker(HubConfig(root, auto_title=False), lambda value: None, lambda error: None,
                backend_factory=settings_backend)
            try:
                await asyncio.wrap_future(worker.prepare_restart())
                with self.assertRaisesRegex(RuntimeError, "closing"):
                    await asyncio.wrap_future(worker.call("new_session", "new", "Must not start"))
                worker.cancel_restart()
                session = await asyncio.wrap_future(worker.call("new_session", "new", "Allowed again"))
                self.assertTrue(session)
            finally:
                await asyncio.to_thread(worker.close)

    async def test_restart_cancels_component_command_and_waits_for_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            worker = BackendWorker(HubConfig(root, auto_title=False), lambda value: None, lambda error: None,
                                   backend_factory=settings_backend)
            entered, cleaned = threading.Event(), threading.Event()
            try:
                await asyncio.wrap_future(worker.call("snapshot"))
                async def install_command():
                    async def blocked(*args):
                        entered.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            await asyncio.sleep(.02)
                            cleaned.set()
                    worker.runtime.manage_records = blocked
                await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(install_command(), worker.loop))
                command = worker.call("manage_records")
                await until(entered.is_set)
                await asyncio.wait_for(asyncio.wrap_future(worker.prepare_restart()), 10)
                self.assertTrue(cleaned.is_set())
                self.assertTrue(command.cancelled())
                self.assertFalse(worker._commands)
            finally:
                await asyncio.to_thread(worker.close)

    async def test_shell_restart_replaces_backend_and_preserves_shell_buffer(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            factory = partial(BackendWorker, backend_factory=settings_backend)
            # HubInstallation imports LiveController locally; replace its default worker factory.
            from hub.ui.live import LiveController
            original_init = LiveController.__init__
            def initialize(controller, view, config):
                original_init(controller, view, config, worker_factory=factory)
            with patch.object(LiveController, "__init__", initialize):
                prompt = Prompt(input=pipe, output=SizedOutput())
                old = install(prompt, config=HubConfig(root, model="test/model", auto_title=False))
                app = prompt.app
                task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: old.view.show(app)))
                try:
                    await until(lambda: old.view.connected and not old.view.projects.busy)
                    prompt.default_buffer.text = "keep shell draft"
                    pipe.send_text('\x10\r')
                    screen = old.view.settings
                    await until(lambda: screen.page is not None and not screen.busy)
                    with set_app(app):
                        screen.choose("general")
                        screen.page.form.fields[("workspace_root",)].input.text = str(Path(root) / "new-root")
                        screen.save()
                    await until(lambda: old.view._dialog is not None)
                    pipe.send_bytes(b'\r')
                    await until(lambda: prompt._hub_ui_preview is not old)
                    new = prompt._hub_ui_preview
                    await until(lambda: new.view.connected)
                    self.assertEqual(new.controller.config.workspace, Path(root) / "new-root")
                    self.assertTrue(old.controller.closed)
                    self.assertFalse(old.controller.worker._thread.is_alive())
                    self.assertTrue(new.view.visible)
                    self.assertFalse(task.done())
                    self.assertEqual(prompt.default_buffer.text, "keep shell draft")
                finally:
                    if not task.done():
                        app.exit(result="")
                        await task
                    prompt._hub_ui_preview.close()
