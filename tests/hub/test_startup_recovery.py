"""Startup keeps failed originals and only retries explicit, owner-scoped recovery."""

import asyncio
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.asset.guides import StarterSkill
from hub.backend.progress import BackendProgress
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.preferences import PreferencesStore, prepare_profile
from hub.config.presets import PresetStore
from hub.config.recovery import FileReset
from hub.config.restart import restart_config
from hub.errors.startup import StartupFailure, StartupLoadError
from hub.locales.keys import MessageKey as K
from hub.ui.application import create_application
from hub.ui.input.keys import KeyName
from hub.widget.dialog import PopupSize
from hub.widget.forms.startup_loading import StartupLoadingDialog
from examples.hub.preview import create_preview
from tests.hub.support import SizedOutput, render_container, eventually, backend_ready


class StartupStorageTests(unittest.TestCase):
    def test_invalid_preferences_remain_unchanged_until_explicit_backup_reset(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            store.path.parent.mkdir()
            original = b'{"general":'
            store.path.write_bytes(original)
            untouched = Path(root) / 'project-data'
            untouched.write_text('keep')
            _, preferences, issue = prepare_profile(HubConfig(root))
            self.assertEqual(preferences.to_dict(), {})
            self.assertEqual(issue.target, str(store.path))
            self.assertEqual(store.path.read_bytes(), original)
            saved = issue.reset()
            self.assertEqual(saved.read_bytes(), original)
            self.assertFalse(store.path.exists())
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
            manifest = json.loads((saved.parent / 'manifest.json').read_text())
            self.assertEqual(manifest['source'], str(store.path))
            self.assertEqual(untouched.read_text(), 'keep')
            self.assertIsNone(prepare_profile(HubConfig(root))[2])

    def test_sidebar_reset_preserves_preferences_and_new_failures_are_observed_on_retry(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            store.save('profile', {'display_name': 'Retained'})
            original = store.path.read_bytes()
            store.sidebar.path.write_text('broken')
            _, _, issue = prepare_profile(HubConfig(root))
            self.assertEqual(issue.target, str(store.sidebar.path))
            # Retry must return an installation config even while settings are
            # still invalid, so the replacement can expose the current owner.
            config = restart_config(HubConfig(root), HubConfig(root), recover_startup=True)
            self.assertEqual(prepare_profile(config)[2].target, str(store.sidebar.path))
            saved = issue.reset()
            self.assertEqual(saved.read_text(), 'broken')
            self.assertEqual(store.path.read_bytes(), original)
            effective, _, failure = prepare_profile(config)
            self.assertIsNone(failure)
            self.assertEqual(effective.user_profile.display_name, 'Retained')

    def test_nested_preset_reset_uses_the_owning_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            store = PresetStore(root)
            store.definitions.mkdir(parents=True)
            source = store.definitions / 'broken.json'
            source.write_text('not json')
            with self.assertRaises(StartupLoadError) as caught:
                store.read(startup=True)
            self.assertEqual(caught.exception.target, str(source))
            saved = caught.exception.reset()
            self.assertEqual(saved.parent.parent, Path(root) / '.hub/recovery-backups')
            self.assertEqual(saved.read_text(), 'not json')
            self.assertFalse(source.exists())
            self.assertIsNotNone(store.read(startup=True))

    def test_reset_rejects_changed_or_symlinked_source(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            store.path.parent.mkdir()
            store.path.write_text('before')
            reset = FileReset.capture(store)
            store.path.write_text('after')
            with self.assertRaisesRegex(ValueError, 'changed'):
                reset()
            self.assertEqual(store.path.read_text(), 'after')
            outside = Path(root) / 'outside'
            outside.write_text('keep')
            store.path.unlink()
            store.path.symlink_to(outside)
            self.assertIsNone(FileReset.capture(store))
            with self.assertRaises(ValueError):
                reset()
            self.assertEqual(outside.read_text(), 'keep')

    def test_backup_write_failure_never_moves_source(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            store.path.parent.mkdir()
            store.path.write_text('original')
            reset = FileReset.capture(store)
            with patch('hub.config.recovery.os.fsync', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    reset()
            self.assertEqual(store.path.read_text(), 'original')

    def test_post_rename_failure_preserves_original_and_backup(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            store.path.parent.mkdir()
            store.path.write_text('original')
            reset = FileReset.capture(store)
            with patch('hub.config.recovery.os.fsync', side_effect=[None, OSError('directory sync')]):
                with self.assertRaisesRegex(OSError, 'directory sync'):
                    reset()
            self.assertEqual(store.path.read_text(), 'original')
            backups = list((store.path.parent / 'recovery-backups').glob('*/preferences.json'))
            self.assertEqual([path.read_text() for path in backups], ['original'])


class StartupUITests(unittest.IsolatedAsyncioTestCase):
    async def test_loading_failure_replaces_all_loading_content_and_keeps_safe_actions(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            self.addCleanup(view.close)
            retry = AsyncMock()
            with set_app(app):
                view.show(app)
                popup = StartupLoadingDialog(view, retry)
                view.dialogs.show(popup, popup.first_button)
                popup.observe(BackendProgress(K.PROGRESS_BACKEND_SKILLS, 3, 9))
                rendered = '\n'.join(render_container(popup, popup._width().preferred, popup._height().preferred))
                self.assertIn('33%', rendered)
                self.assertFalse(popup.retry_button.enabled())
                popup.retry_button._activate()
                retry.assert_not_called()
                issue = StartupFailure.observe(ValueError('precise startup cause'),
                                               K.PROGRESS_BACKEND_SKILLS, '/example')
                popup.failed(issue)
                rows = render_container(popup, popup._width().preferred, popup._height().preferred)
                rendered = '\n'.join(rows)
                self.assertIn('precise startup cause', rendered)
                self.assertNotIn('33%', rendered)
                self.assertNotIn(view.t(K.PROGRESS_BACKEND_SKILLS), rendered)
                self.assertEqual(next(row.index('precise startup cause') for row in rows
                                      if 'precise startup cause' in row), 2)
                self.assertTrue(popup.retry_button.enabled())
                self.assertFalse(popup.reset_button.enabled())
                self.assertEqual(popup.size, PopupSize.CUSTOM)
                self.assertFalse(popup.progress.active)
                self.assertIsNone(popup.progress._timer)
                self.assertFalse(view.progress.active)
                self.assertFalse(''.join(part[1] for part in
                    popup.status_bar.get_container().content.create_content(56, 1).get_line(0)))

    async def test_bad_initial_settings_do_not_start_worker_or_overwrite_original(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            store = PreferencesStore(root)
            store.path.parent.mkdir()
            store.path.write_text('broken')
            worker = Mock(side_effect=AssertionError('must not create backend'))
            app, controller = create_application(HubConfig(root), input=pipe,
                output=SizedOutput(), worker_factory=worker)
            task = asyncio.create_task(app.run_async(pre_run=lambda: controller.view.show(app)))
            try:
                await eventually(lambda: controller.view._dialog is not None)
                popup = controller.view._dialog
                self.assertTrue(popup.showing_failure)
                self.assertTrue(popup.reset_button.enabled())
                self.assertEqual(store.path.read_text(), 'broken')
                self.assertEqual(len(controller.view.operation_diagnostics.records), 1)
                worker.assert_not_called()
                pipe.send_text('\x17')  # Normal restart cannot replace recovery.
                pipe.send_text('\x11')
                await eventually(lambda: not controller.view.visible)
                pipe.send_text('\x11')
                await eventually(lambda: controller.view.visible)
                self.assertIs(controller.view._dialog, popup)
                self.assertEqual(len(controller.view.operation_diagnostics.records), 1)
            finally:
                app.exit()
                await task
                await controller.aclose()

    async def test_project_edit_opens_general_settings_not_name_dialog(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(root, auto_title=False, prepare_provider=False, language="ko"),
                input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await backend_ready(lambda: view.connected and view.projects.loaded and not view.projects.busy,
                                    'project navigation')
                with set_app(app):
                    view.settings.query = 'does-not-match'
                    view.projects.perform(KeyName.EDIT)
                await backend_ready(lambda: view.settings_open and view.settings.page is not None
                                    and not view.settings.busy, 'project general settings')
                self.assertIsNone(view._dialog)
                self.assertEqual(view.settings.page_title(), '프로젝트 • 일반')
                self.assertEqual(view.settings.page.identifier, view.project_id)
                self.assertEqual(view.settings.query, '')
            finally:
                app.exit()
                await task
                await controller.aclose()

    async def test_missing_packaged_skill_fails_before_project_write(self):
        with tempfile.TemporaryDirectory() as root:
            backend = None
            def factory(config):
                nonlocal backend
                from hub.backend.bootstrap import create_backend
                backend = create_backend(config)
                backend.projects.acreate = AsyncMock(wraps=backend.projects.acreate)
                return backend
            runtime = HubRuntime(HubConfig(root, auto_title=False, prepare_provider=False), factory)
            self.addAsyncCleanup(runtime.close)
            skill = StarterSkill(Path(root) / 'missing-skill')
            with patch('hub.asset.guides.STARTER_SKILLS', (skill,)):
                with self.assertRaises(StartupLoadError) as caught:
                    await runtime.start()
            self.assertIn('missing-skill', caught.exception.target)
            self.assertIsNone(caught.exception.reset)
            self.assertEqual(caught.exception.hint, K.STARTUP_INSTALL_HINT)
            backend.projects.acreate.assert_not_awaited()

    async def test_confirmed_reset_reinstalls_hub_and_preserves_shell_draft(self):
        from hub.hub import install
        from tests.hub.test_ish_integration import Prompt
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            store = PreferencesStore(root)
            store.path.parent.mkdir()
            original = b'{"general":'
            store.path.write_bytes(original)
            prompt = Prompt(input=pipe, output=SizedOutput())
            old = install(prompt, config=HubConfig(root, auto_title=False, prepare_provider=False))
            app = prompt.app
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: old.view.show(app)))
            try:
                await eventually(lambda: old.view._dialog is not None)
                prompt.default_buffer.text = 'keep shell draft'
                with set_app(app):
                    old.view._dialog.confirm_reset()
                self.assertEqual(store.path.read_bytes(), original)
                pipe.send_text('\r')
                await backend_ready(lambda: prompt._hub_ui_preview is not old
                                    and prompt._hub_ui_preview.view.connected, 'reset and reinstall')
                new = prompt._hub_ui_preview
                self.assertTrue(old.controller.closed)
                self.assertIsNone(new.view._dialog)
                self.assertEqual(prompt.default_buffer.text, 'keep shell draft')
                self.assertFalse(task.done())
                backups = list((store.path.parent / 'recovery-backups').glob('*/preferences.json'))
                self.assertEqual([path.read_bytes() for path in backups], [original])
            finally:
                if not task.done():
                    app.exit(result='')
                    await task
                await prompt._hub_ui_preview.controller.aclose()
                prompt._hub_ui_preview.close()
