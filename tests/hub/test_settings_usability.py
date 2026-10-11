"""Settings navigation, named creation and isolated asynchronous width saves."""

import asyncio
from dataclasses import asdict
from functools import partial
import tempfile
import threading
import unittest
from unittest.mock import patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig
from hub.backend.worker import BackendWorker
from hub.config.preferences import PreferencesStore
from hub.config.profile import UserProfile
from hub.config.theme import HubTheme
from hub.ui.application import create_application
from tests.hub.support import SizedOutput, eventually
from tests.hub.test_live import controlled_backend, until


class SettingsUsabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_and_project_creation_require_a_popup_name(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            gate = threading.Event()
            gate.set()
            app, controller = create_application(HubConfig(directory, auto_title=False), input=pipe,
                output=SizedOutput(), worker_factory=partial(BackendWorker,
                    backend_factory=partial(controlled_backend, gate=gate)))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_text('\x13')
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose('profile')
                    screen.layout.focus_main()
                current = app.layout.current_control
                pipe.send_text('\x1b[C')
                await asyncio.sleep(.1)
                self.assertIs(app.layout.current_control, current)
                field = screen.page.form.fields[('display_name',)]
                field.input.text = 'abc'
                pipe.send_text('\r')
                await eventually(lambda: field.editing)
                field.input.buffer.cursor_position = 0
                pipe.send_text('\x1b[C')
                await eventually(lambda: field.input.buffer.cursor_position == 1)
                with set_app(app):
                    screen.finish_edit()
                    screen._left_key = screen.projects[0]['id']
                    app.layout.focus(screen.project_list)
                count = len(screen.projects)
                pipe.send_text('c')
                await eventually(lambda: view._dialog is not None)
                name = app.layout.current_control.buffer
                name.text = '  '
                pipe.send_text('\r')
                await asyncio.sleep(.1)
                self.assertIsNotNone(view._dialog)
                self.assertEqual(len(screen.projects), count)
                self.assertIn(screen.t('settings_title_required'), screen.status)
                name.text = '  Named project  '
                pipe.send_text('\r')
                await until(lambda: view._dialog is None and not screen.busy)
                self.assertEqual(len(screen.projects), count + 1, screen.status)
                self.assertIsNone(view._dialog)
                self.assertEqual(screen.page.record['values']['title'], 'Named project')
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_width_only_is_saved_and_pending_writes_coalesce_without_committing_drafts(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            store = PreferencesStore(directory)
            store.save('profile', asdict(UserProfile(display_name='Saved')))
            store.save('appearance', asdict(HubTheme()))
            before = store.path.read_bytes()
            gate, entered, release = threading.Event(), threading.Event(), threading.Event()
            gate.set()
            writes = []
            save = PreferencesStore.save_sidebar_width
            def blocked(instance, width):
                writes.append(width)
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('Width write was not released')
                return save(instance, width)
            app, controller = create_application(HubConfig(directory, auto_title=False), input=pipe,
                output=SizedOutput(), worker_factory=partial(BackendWorker,
                    backend_factory=partial(controlled_backend, gate=gate)))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_text('\x13')
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose('appearance')
                    appearance = screen.page
                    appearance.form.fields[('comment',)].input.text = '#123456'
                    screen.choose('profile')
                    profile = screen.page
                    profile.form.fields[('display_name',)].input.text = 'Unsaved'
                    screen.switch_panel()
                with patch.object(PreferencesStore, 'save_sidebar_width', blocked):
                    pipe.send_text('\x1b[1;5C')
                    await eventually(entered.is_set)
                    pipe.send_text('\x1b[1;5C' * 3)
                    await eventually(lambda: view.theme.sidebar_width == 30)
                    self.assertFalse(screen.busy)
                    self.assertTrue(profile.dirty)
                    self.assertTrue(appearance.dirty)
                    self.assertEqual(appearance.form.fields[('sidebar_width',)].initial, '30')
                    self.assertEqual(store.path.read_bytes(), before)
                    release.set()
                    await until(lambda: store.load()['appearance']['sidebar_width'] == 30)
                self.assertLessEqual(len(writes), 2)
                self.assertEqual(store.path.read_bytes(), before)
                pipe.send_text('\x03')
                await eventually(lambda: not view.settings_open)
                self.assertEqual(view.theme.sidebar_width, 30)
                self.assertEqual(store.load()['profile']['display_name'], 'Saved')
                self.assertEqual(store.load()['appearance']['comment'], HubTheme().comment)
                pipe.send_text('\x1b')
                await eventually(lambda: app.layout.current_control == view._session_control)
                pipe.send_text('\x1b[1;5D')
                await until(lambda: store.load()['appearance']['sidebar_width'] == 29)
                self.assertEqual(store.path.read_bytes(), before)
            finally:
                release.set()
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
            self.assertEqual(PreferencesStore(directory).load()['appearance']['sidebar_width'], 29)
